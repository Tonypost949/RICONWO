#!/usr/bin/env python3
"""Read-only fingerprint probe: ArcGIS Server version / admin exposure across RICO GIS roots.
Compares against cracked installer set in GIS_SOFTWARE_AND_MUNICIPAL_INFRASTRUCTURE_AUDIT_MANIFEST.md.
No authentication attempted, no writes."""
import json, glob, re, os, sys, time, ssl, socket
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request, urllib.error

EVID = '/home/osintneoai/evidence/gis_audit_20261019'
OUT = os.path.join(EVID, 'cracked_software_fingerprint_probe.json')
UA = 'Mozilla/5.0 (X11; Linux x86_64) forensic-audit/1.0'

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch(url, timeout=25):
    t0 = time.time()
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json,*/*'})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read(300000)
            return {'url': url, 'status': r.status, 'headers': dict(r.headers),
                    'body': body.decode('utf-8', 'replace'), 'elapsed': round(time.time()-t0, 2)}
    except urllib.error.HTTPError as e:
        try: body = e.read(300000).decode('utf-8', 'replace')
        except Exception: body = ''
        return {'url': url, 'status': e.code, 'headers': dict(e.headers or {}),
                'body': body, 'elapsed': round(time.time()-t0, 2), 'error': 'HTTPError'}
    except Exception as e:
        return {'url': url, 'status': None, 'headers': {}, 'body': '', 'elapsed': round(time.time()-t0, 2),
                'error': f'{type(e).__name__}: {e}'}

def roots_from_evidence():
    roots = set()
    pat = re.compile(r'https://[A-Za-z0-9\.\-]+(?::\d+)?(?:/[A-Za-z0-9_\.\-]+)*?/(?:arcgis|server)/rest/services')
    for f in glob.glob(os.path.join(EVID, '*.json')) + glob.glob(os.path.join(EVID, '..', '..', 'repos', 'RICONWO', '*.md')):
        try: txt = open(f, errors='ignore').read()
        except Exception: continue
        for m in pat.finditer(txt):
            roots.add(m.group(0))
    for u in ('https://gis.huntingtonbeachca.gov/arcgis/rest/services',
              'https://ocgis.com/arcpub/rest/services'):
        roots.add(u)
    clean = []
    for r in sorted(roots):
        r = r.rstrip('/')
        host = re.match(r'https://([^/]+)', r).group(1).lower()
        if host.endswith('maps.arcgis.com') or host in ('www.arcgis.com', 'arcgis.com'):
            continue
        clean.append(r)
    return clean

def probe_root(root):
    # info endpoint: .../rest/services -> .../rest/info
    base = root[:-len('/rest/services')]
    info_url = base + '/rest/info?f=json'
    admin_url = base + '/admin?f=json'
    lic_url = base + '/admin/license?f=json'
    svc_url = root + '?f=pjson'
    rec = {'root': root, 'base': base}
    r = fetch(info_url)
    rec['info'] = {k: r[k] for k in ('url', 'status', 'error', 'elapsed') if k in r}
    rec['info']['server_header'] = r['headers'].get('Server')
    rec['info']['content_type'] = r['headers'].get('Content-Type')
    try:
        j = json.loads(r['body'])
        rec['info']['fullVersion'] = j.get('fullVersion')
        rec['info']['currentVersion'] = j.get('currentVersion')
        rec['info']['authInfo'] = j.get('authInfo')
        rec['info']['soapUrl'] = j.get('soapUrl')
        rec['info']['isTokenBasedSecurity'] = (j.get('authInfo') or {}).get('isTokenBasedSecurity')
    except Exception:
        rec['info']['body_head'] = r['body'][:300]
    r2 = fetch(svc_url)
    rec['services'] = {'url': svc_url, 'status': r2['status'], 'error': r2.get('error')}
    try:
        j2 = json.loads(r2['body'])
        rec['services']['currentVersion'] = j2.get('currentVersion')
        rec['services']['folders'] = j2.get('folders')
        rec['services']['service_count'] = len(j2.get('services') or [])
    except Exception:
        rec['services']['body_head'] = r2['body'][:200]
    ra = fetch(admin_url, timeout=15)
    rec['admin'] = {'url': admin_url, 'status': ra['status'], 'error': ra.get('error')}
    try:
        ja = json.loads(ra['body'])
        rec['admin']['error_code'] = (ja.get('error') or {}).get('code')
        rec['admin']['error_message'] = (ja.get('error') or {}).get('message')
    except Exception:
        rec['admin']['body_head'] = ra['body'][:160]
    rl = fetch(lic_url, timeout=15)
    rec['admin_license'] = {'url': lic_url, 'status': rl['status'], 'error': rl.get('error')}
    try:
        jl = json.loads(rl['body'])
        if isinstance(jl, dict) and jl.get('error'):
            rec['admin_license']['error_code'] = jl['error'].get('code')
            rec['admin_license']['error_message'] = jl['error'].get('message')
        else:
            rec['admin_license']['body_head'] = rl['body'][:300]
    except Exception:
        rec['admin_license']['body_head'] = rl['body'][:160]
    return rec

def main():
    roots = roots_from_evidence()
    print(f'{len(roots)} unique self-hosted roots', flush=True)
    out = {'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'method': 'read-only GET /rest/info?f=json, /rest/services?f=pjson, /admin?f=json, /admin/license?f=json',
           'root_count': len(roots), 'results': []}

    def flush():
        tmp = OUT + '.tmp'
        out['results'].sort(key=lambda x: x.get('root', ''))
        json.dump(out, open(tmp, 'w'), indent=1)
        os.replace(tmp, OUT)

    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(probe_root, r): r for r in roots}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                rec = fut.result()
            except Exception as e:
                rec = {'root': futs[fut], 'fatal': f'{type(e).__name__}: {e}'}
            out['results'].append(rec)
            if i % 5 == 0 or i == len(roots):
                flush()
            print(f'[{i}/{len(roots)}] {rec.get("root")} -> {rec.get("info",{}).get("fullVersion") or rec.get("info",{}).get("status") or rec.get("fatal")}', flush=True)
    flush()
    print('WROTE', OUT, flush=True)

if __name__ == '__main__':
    main()
