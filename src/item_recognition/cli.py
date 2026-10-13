"""Analyze saved image files only. No game input, counters, or library writes."""
import argparse
import json
from pathlib import Path
if __package__:
    from .recognizer import ItemRecognizer,file_hash,DEFAULT_LIBRARY,DEFAULT_TEMPLATES,PROJECT
else:
    from recognizer import ItemRecognizer,file_hash,DEFAULT_LIBRARY,DEFAULT_TEMPLATES,PROJECT

ROOT=Path(__file__).resolve().parent

def analyze_paths(paths,output,recognizer,force=False):
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    originals={Path(p).resolve() for p in paths}
    manifest_path=output/'processing_manifest.json'
    if manifest_path in originals or output/'batch_results.json' in originals: raise ValueError('Output overlaps original input')
    manifest=json.loads(manifest_path.read_text('utf-8')) if manifest_path.exists() else {'version':1,'processed':{}}
    results=[]
    seen=set()
    for path in sorted(set(Path(p).resolve() for p in paths),key=str):
        try: digest=file_hash(path)
        except OSError: digest=None
        key=digest or str(path)
        previous=manifest['processed'].get(key)
        if key in seen or (previous and previous.get('status')!='error' and not force):
            results.append({'screenshot':str(path),'screenshot_sha256':digest,'status':'duplicate','duplicate_of':previous.get('screenshot') if previous else None,'json_path':previous.get('json_path') if previous else None,'rewards':[],'unknown_rewards':[],'totals':{},'warnings':['Same screenshot bytes already processed; no repeated numerical credit. A content hash is not a game-run identifier.']})
            continue
        seen.add(key)
        stem=path.stem[:70]+'-'+(digest[:16] if digest else 'unreadable')
        json_path=output/(stem+'.json'); debug_path=output/(stem+'.debug.png')
        if json_path in originals or debug_path in originals: raise ValueError('Output overlaps original input')
        result=recognizer.analyze_screenshot(path,debug_path=debug_path)
        json_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        manifest['processed'][key]={'screenshot':str(path),'json_path':str(json_path),'debug_path':str(debug_path),'status':result['status']}
        results.append(result)
    temp=manifest_path.with_suffix('.tmp'); temp.write_text(json.dumps(manifest,indent=2),encoding='utf-8'); temp.replace(manifest_path)
    (output/'batch_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    return results

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path); parser.add_argument('--output',type=Path,default=PROJECT/'runtime/item_recognition/manual')
    parser.add_argument('--library',type=Path,default=DEFAULT_LIBRARY); parser.add_argument('--recursive',action='store_true')
    parser.add_argument('--force',action='store_true'); parser.add_argument('--no-calibration',action='store_true')
    parser.add_argument('--calibration',type=Path,default=DEFAULT_TEMPLATES/'calibration.json')
    args=parser.parse_args()
    if args.input.is_dir():
        iterator=args.input.rglob('*') if args.recursive else args.input.glob('*')
        files=[p for p in iterator if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp') and not p.resolve().is_relative_to(args.output.resolve())]
    else: files=[args.input]
    if not files: parser.error('No saved image files found')
    recognizer=ItemRecognizer(args.library,None if args.no_calibration else args.calibration if args.calibration.exists() else None)
    results=analyze_paths(files,args.output,recognizer,args.force)
    print(json.dumps({'screenshots':len(results),'statuses':{s:sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})},'output':str(args.output.resolve())}))

if __name__=='__main__': main()
