"""Read-only screenshot inventory and contact sheets; never interacts with NB:R."""
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent
REPORTS = ROOT / 'reports'
SOURCE_DIRS = ('development', 'reference_images', 'runtime', 'patterns')

def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    previous = {}
    prior_path = REPORTS / 'resources.json'
    if prior_path.exists():
        previous = json.loads(prior_path.read_text(encoding='utf-8'))
    reviewed = {e.get('sha256'): e for e in previous.get('files',[]) if e.get('inspection_status') == 'manually_reviewed'}
    inventory, unique, seen = [], [], {}
    for directory in SOURCE_DIRS:
        for path in sorted((SOURCE / directory).rglob('*')):
            if not path.is_file() or path.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
                continue
            entry = {'path': str(path), 'relative_path': path.relative_to(SOURCE).as_posix()}
            try:
                raw = path.read_bytes()
                with Image.open(path) as image:
                    entry.update(width=image.width, height=image.height, mode=image.mode)
                entry['sha256'] = hashlib.sha256(raw).hexdigest()
                entry['kind'] = 'full_screen_candidate' if entry['width'] >= 300 and entry['height'] >= 500 else 'cropped_reference'
                entry['inspection_status'] = 'not_yet_manually_reviewed'
                if entry['sha256'] in reviewed:
                    for key in ('inspection_status','screen_type','stage','visual_notes'):
                        if key in reviewed[entry['sha256']]:
                            entry[key] = reviewed[entry['sha256']][key]
                duplicate = seen.get(entry['sha256'])
                if duplicate:
                    entry['exact_duplicate_of'] = duplicate
                else:
                    seen[entry['sha256']] = entry['path']
                    if entry['kind'] == 'full_screen_candidate':
                        unique.append(entry)
            except Exception as error:
                entry['error'] = str(error)
            inventory.append(entry)
    # Prioritize reward variants; paths are not used as visual ground truth.
    unique.sort(key=lambda e: (not any(s in e['relative_path'] for s in ('reward','results','completion','resume')),e['relative_path']))
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 12)
    sheets = []
    for start in range(0, len(unique), 24):
        subset = unique[start:start + 24]
        sheet = Image.new('RGB', (1200, 1560), '#202328')
        draw = ImageDraw.Draw(sheet)
        for index, entry in enumerate(subset):
            x, y = (index % 6) * 200, (index // 6) * 390
            with Image.open(entry['path']) as source:
                thumb = source.convert('RGB')
                thumb.thumbnail((190, 335))
                sheet.paste(thumb, (x + (200-thumb.width)//2, y))
            number = start + index
            entry['contact_index'] = number
            entry['contact_sheet'] = f'contacts_{start//24:02d}.jpg'
            text = entry['relative_path'].replace('development/', '').replace('runtime/screenshots/', 'runtime/')
            chunks = [text[i:i+29] for i in range(0,len(text),29)]
            draw.text((x+4,y+338),f'{number:03d}: '+'\n'.join(chunks[:3]),font=font,fill='white')
        output = REPORTS / f'contacts_{start//24:02d}.jpg'
        sheet.save(output, quality=88)
        sheets.append(str(output))
    output = {'scope': 'Existing NB:R local images only; no processes/devices accessed', 'total_image_files': len(inventory), 'full_screen_candidates': sum(e.get('kind')=='full_screen_candidate' for e in inventory), 'unique_full_screen_candidates': len(unique), 'exact_duplicate_files': sum('exact_duplicate_of' in e for e in inventory), 'contact_sheets':sheets, 'files':inventory}
    for key in ('manual_review_summary','ground_truth','screen_geometry','limitations'):
        if key in previous:
            output[key] = previous[key]
    if 'manual_review_summary' in output:
        screens = [e for e in unique if e.get('inspection_status') == 'manually_reviewed']
        results = [e for e in screens if e.get('screen_type','').startswith(('results_','acquired_'))]
        output['manual_review_summary'].update(
            unique_full_screens_reviewed=len(screens),
            unique_results_ui_screens=len(results),
            unique_results_without_dialog_overlay=sum('dialog_overlay' not in e['screen_type'] for e in results),
            unique_revealed_acquired_screens=sum(e['screen_type']=='acquired_revealed' for e in results),
            results_ui_files_including_exact_duplicates=sum(e.get('screen_type','').startswith(('results_','acquired_')) for e in inventory))
    (REPORTS/'resources.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k!='files'}))

if __name__ == '__main__':
    main()
