import base64
import http.client
import io
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import threading
import tempfile
import time
import traceback
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import pymupdf as fitz
from review_results import build_review_results, drawable_results, publish_results, COLORS
from dimension_checks import display_segments, linear_chain_evidence
from dimension_target_checks import dimension_target_evidence
from drawing_evidence import build_evidence, write_json
from drawing_evidence_tool import TOOL as EVIDENCE_TOOL, TOOLS as DRAWING_TOOLS, call_drawing_tool, drawing_model_diff, evidence_slice, model_summaries
from graphic_checks import graphic_inventory_read, overlaps, page_graphics, watermark_evidence
from hole_checks import repeated_hole_counts
from signature_checks import ROLES, inspect_signature_cells
from size_constraint_checks import constraint_findings
from slot_checks import inspect_slots, slot_size_pairs
from slot_location_checks import slot_location_evidence
from template_checks import COMPANY, TEMPLATE, inspect_sheet_format
from title_field_checks import inspect_title_fields

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
LOCK = threading.Lock()  # SolidWorks is a serialized desktop COM server.
VISION_LOCK = threading.Lock()
SETTINGS_LOCK = threading.Lock()
JOB_SAVE_LOCK = threading.Lock()
JOBS = {}
for p in DATA.glob('*/job.json'):
    try:
        j = json.loads(p.read_text(encoding='utf-8'))
        if j['status'] in ('extracting', 'converting', 'reviewing', 'queued'):
            j.update(status='failed', error='服务重启，任务中断；请重新上传。')
        JOBS[j['id']] = j
    except Exception:
        pass


def save(j, **kw):
    target = DATA / j['id'] / 'job.json'
    with JOB_SAVE_LOCK:
        write_json(target, dict(j, **kw))
        j.update(kw)


def safe_extract(archive, dest):
    total = 0
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist()) > 5000:
            raise ValueError('压缩包文件数超过5000。')
        seen = set()
        for item in z.infolist():
            name = item.filename.replace('\\', '/')
            if not item.flag_bits & 0x800:
                try:
                    name = name.encode('cp437').decode('gbk')
                except (UnicodeError, LookupError):
                    pass
            p = pathlib.PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or ':' in name or not p.parts:
                raise ValueError('压缩包包含不安全路径。')
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('不支持压缩包中的符号链接。')
            key = name.casefold().rstrip('/')
            if key in seen:
                raise ValueError('压缩包包含重复路径。')
            seen.add(key)
            total += item.file_size
            if total > 2 * 1024 ** 3:
                raise ValueError('解压总大小超过2GB。')
            target = dest.joinpath(*p.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)


def prepare_cad(base, source, tag):
    """Use unique document titles and redirect native CAD references in job copies only."""
    manifest=base/'cad-manifest.json'
    if manifest.exists():
        entries=[x for x in json.loads(manifest.read_text(encoding='utf-8')) if not pathlib.Path(x['source']).name.startswith('~$')]
        manifest.write_text(json.dumps(entries,ensure_ascii=False),encoding='utf-8')
    else:
        work=base/'cad';work.mkdir(exist_ok=True)
        entries=[]
        for i,p in enumerate(sorted(p for p in source.rglob('*') if p.suffix.lower() in ('.slddrw','.sldprt','.sldasm') and not p.name.startswith('~$'))):
            dest=work/f'{p.stem}__{tag[:12]}_{i:03d}{p.suffix}'
            shutil.copy2(p,dest)
            entries.append(dict(source=str(p),work=str(dest),relative=str(p.relative_to(source)),prepared=False))
        manifest.write_text(json.dumps(entries,ensure_ascii=False),encoding='utf-8')
    if any(x.get('referenceVersion')!=2 for x in entries):
        for entry in entries:
            shutil.copy2(entry['source'],entry['work'])
            entry['prepared']=False
        manifest.write_text(json.dumps(entries,ensure_ascii=False),encoding='utf-8')
    if not all(x.get('prepared') for x in entries):
        result=subprocess.run(['powershell.exe','-NoProfile','-STA','-ExecutionPolicy','Bypass','-File',str(ROOT/'bridge/prepare.ps1'),'-ManifestPath',str(manifest)],capture_output=True,timeout=300)
        (base/'cad-prepare.log').write_bytes(result.stdout+result.stderr)
        if result.returncode:
            # Do not reuse partially rewritten files on the next attempt.
            for entry in entries:
                shutil.copy2(entry['source'],entry['work'])
            raise RuntimeError('工作副本模型引用准备失败，请检查压缩包中的配套模型和 cad-prepare.log。')
        entries=json.loads(manifest.read_text(encoding='utf-8-sig'))
        if isinstance(entries,dict):entries=[entries]
        for entry in entries:entry.update(prepared=True,referenceVersion=2)
        manifest.write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
    return {str(pathlib.Path(x['source'])):pathlib.Path(x['work']) for x in entries}


def convert(j, retry=False):
    try:
        base = DATA / j['id']
        src = base / 'source'
        src.mkdir(exist_ok=True)
        save(j, status='extracting', message='正在解压并识别图纸与配套模型…', progress=3)
        if not retry:safe_extract(base / 'upload.zip', src)
        drawings = sorted(p for p in src.rglob('*') if p.suffix.lower() == '.slddrw' and not p.name.startswith('~$'))
        if not drawings:
            raise ValueError('压缩包中未发现 .SLDDRW 工程图。请同时包含配套 .SLDPRT / .SLDASM。')
        if len(drawings) > 100:
            raise ValueError('单批最多100份工程图。')
        files = j['files'] if retry else [dict(id=str(i), name=p.name, relative=str(p.relative_to(src)), status='queued', pages=0) for i, p in enumerate(drawings)]
        save(j, files=files, message='等待 SolidWorks 转换服务…', status='queued')
        with LOCK:
            save(j,message='正在建立任务独立的 CAD 工作副本并关联包内模型…')
            cad=prepare_cad(base,src,j['id'])
            manifest=json.loads((base/'cad-manifest.json').read_text(encoding='utf-8'))
            save(j,referenceWarnings=[warning for entry in manifest for warning in entry.get('warnings',[])])
            for i, (p, item) in enumerate(zip(drawings, files)):
                if retry and item['status']=='ready':continue
                item['status'] = 'converting'
                save(j, status='converting', message=f'正在转换 {item["name"]}', progress=5 + int(i / len(files) * 90))
                out = base / f'{i}.pdf'
                info = base / f'{i}.json'
                try:
                    command = ['powershell.exe', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass',
                               '-File', str(ROOT / 'bridge/convert.ps1'), '-SourcePath', str(cad[str(p)]),
                               '-PdfOut', str(out), '-InfoOut', str(info)]
                    log_path = base / f'{i}.log'
                    limited = False
                    with log_path.open('wb') as log:
                        try:
                            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=300)
                        except subprocess.TimeoutExpired:
                            log.seek(0, 2)
                            log.write(b'\nSolidWorks conversion timed out after 300 seconds.\n')
                            log.flush()
                            lines = [line.strip() for line in log_path.read_bytes().splitlines() if line.strip()]
                            topology_stages = (b'Model inventory: reading body faces and openings',
                                               b'Model inventory: reading spatial geometry')
                            if len(lines)<2 or lines[-2] not in topology_stages:
                                raise RuntimeError('SolidWorks 转换超过 300 秒，请查看转换日志中的最后阶段。') from None
                            limited = True
                            log.write(b'Retrying without body-face and visible-edge inventories.\n')
                            log.flush()
                            try:
                                result = subprocess.run(command+['-SkipBodyOpenings'], stdout=log,
                                                        stderr=subprocess.STDOUT, timeout=300)
                            except subprocess.TimeoutExpired:
                                log.seek(0, 2)
                                log.write(b'\nReduced conversion also timed out after 300 seconds.\n')
                                raise RuntimeError('跳过孔槽深度提取后仍转换超时，请查看日志。') from None
                    if result.returncode:
                        raise RuntimeError('SolidWorks API调用失败，详见转换日志。')
                    # Print spooler may complete after the API returns.
                    for attempt in range(40):
                        try:
                            d = fitz.open(out)
                            if len(d) and all(len(page.get_drawings()) > 0 for page in d):
                                break
                        except Exception:
                            pass
                        time.sleep(.5)
                    else:
                        raise RuntimeError('打印输出尚未完成或PDF无有效图元。')
                    with d:
                        evidence = build_evidence(d, drawing_pages(base, item), item['name'], template_regions)
                        if evidence['pageAlignment'] != 'sheet-order':
                            raise RuntimeError('PDF 页数与 SolidWorks 图纸页数不一致。')
                        write_json(base / 'evidence' / f'{i}.json', evidence)
                        item.update(status='ready', pages=len(d), sizes=[[round(x.rect.width, 2), round(x.rect.height, 2)] for x in d])
                        if limited:
                            item['extractionWarning'] = '深度几何提取超时，已跳过实体面孔槽清单和可见边清单。相关审核需核对证据。'
                        else:
                            item.pop('extractionWarning',None)
                    item.pop('error',None)
                except Exception as e:
                    item.update(status='failed', error=str(e))
                save(j)
        ready = sum(f['status'] == 'ready' for f in files)
        save(j, status='awaiting_confirmation' if ready else 'failed', progress=100, message=f'{ready}/{len(files)} 份图纸已转换；请检查PDF后确认审图。', error=None if ready else '所有图纸转换失败。')
    except Exception as e:
        save(j, status='failed', error=str(e), message='处理失败')


def issue(page, rule, title, reason, box, method='规则引擎', severity='error'):
    return dict(page=page, rule=rule, title=title, reason=reason, box=box, method=method, severity=severity)


def template_regions(raw, width, height):
    """Find the inner border and its lower-right table from template segments."""
    horizontal, vertical = [], []
    for first, second in re.findall(r'^FrameLine: (.+) -> (.+)$', raw, re.M):
        try:
            x, y, _ = map(float, first.split(','))
            u, v, _ = map(float, second.split(','))
        except ValueError:
            continue
        if abs(y-v) < 1e-6:
            horizontal.append((min(x,u), max(x,u), y))
        if abs(x-u) < 1e-6:
            vertical.append((min(y,v), max(y,v), x))
    xs = sorted(x for low, high, x in vertical if high-low > .8*height and 1e-6 < x < width-1e-6)
    ys = sorted(y for low, high, y in horizontal if high-low > .8*width and 1e-6 < y < height-1e-6)
    if len(xs) < 2 or len(ys) < 2:
        return None, None
    left, right, bottom, top = xs[0], xs[-1], ys[0], ys[-1]
    tables = [(x, bottom, right, high) for low, high, x in vertical
              if abs(low-bottom) < 1e-6 and left+.15*width < x < right-.25*width
              and .02 < high-bottom < .25*height
              and any(abs(a-x)<1e-6 and abs(b-right)<1e-6 and abs(y-high)<1e-6
                      for a,b,y in horizontal)]
    return (left,bottom,right,top), min(tables, default=None)


def drawing_pages(base, item):
    name = f'{item["id"]}.json'
    candidates = tuple(base / f'api-v{revision}' / name for revision in range(15,1,-1)) + (base / name,)
    path = next((candidate for candidate in candidates if candidate.exists()), base / name)
    pages = json.loads(path.read_text(encoding='utf-8-sig'))
    return [pages] if isinstance(pages, dict) else pages


def engine(base, item, selected, *, pages=None, packet=None):
    if pages is None:
        pages = drawing_pages(base, item)
    dimension_rules = set(selected).intersection((5,6,7))
    if dimension_rules and packet is None:
        path = base / f'{item["id"]}.pdf'
        if path.exists():
            with fitz.open(path) as pdf:
                packet = build_evidence(pdf, pages, item['name'], template_regions)
    findings = []
    coverage = []
    hole_claims = []
    slot_count_checks = []
    slot_dimension_checks = []
    wizard_size_checks = []
    location_checks = list(slot_location_evidence(pages)) if 5 in selected else []
    signature_checks = []
    signature_graphics = []
    signature_pages_read = 0
    watermark_checks = []
    graphic_pages = 0
    template_checks = []
    title_field_checks = []
    chain_checks = []
    dimension_targets = []
    dimensional_holes = []
    chain_count = 0
    if 3 in selected or 6 in selected:
        _, bindings = inspect_slots(pages)
        pairs = slot_size_pairs(bindings)
        for pair in pairs:
            width, length = pair['width'], pair['length']
            if 6 in selected:
                slot_dimension_checks.append(dict(
                    feature='straight_slot', result='width_and_length_bound', **pair))
            if 3 not in selected or width['quantity'] < 2:
                continue
            quantity = width['quantity']
            recognized = width['modelRecognizedSameSize']
            result = (pair['scope'] if pair['scope'] in ('matched_visible_set', 'matched_base_visible_set')
                      else 'exceeds_recognized_inventory' if quantity > recognized
                      else 'unresolved_scope')
            slot_count_checks.append(dict(feature='straight_slot', result=result, **pair))
            if result != 'exceeds_recognized_inventory':
                continue
            page = pages[width['page']-1]
            paper = re.search(r'^SheetSize_m: ([\d.Ee+-]+),([\d.Ee+-]+)', page['raw'], re.M)
            if not paper:
                continue
            w, h = map(float, paper.groups())
            x, y = width['anchor']
            u, v = x/w, 1-y/h
            if not (0 <= u <= 1 and 0 <= v <= 1):
                continue
            finding = issue(width['page'], 3, '腰孔标注数量超过已识别的同规格实体',
                            f'宽度和总长均标了 {quantity} 个。模型识别到 {recognized} 个 '
                            f'{width["valueMm"]:g} × {length["valueMm"]:g} mm 的腰孔。'
                            '请核对标注分组及模型中是否存在其他轮廓形式的槽。',
                            [max(0,u-.04),max(0,v-.025),min(1,u+.04),min(1,v+.025)], severity='warning')
            finding.update(view=width['view'], objectId=width['objectId'],
                           relatedObjectIds=[length['objectId']], slotId=width['slotId'])
            findings.append(finding)
    for i, page in enumerate(pages, 1):
        raw = page['raw']
        size = re.search(r'SheetSize_m: ([\d.Ee+-]+),([\d.Ee+-]+)', raw)
        sw, sh = map(float, size.groups()) if size else (.297, .210)
        notes = re.findall(r'^Note: (.*?)\nNoteExtent: ([^\n]+)', raw, re.S | re.M)
        frame, title_block = template_regions(raw, sw, sh)
        inspect_graphics = any(rule in selected for rule in (8, 9, 11))
        graphics = page_graphics(raw) if inspect_graphics else []
        graphics_read = graphic_inventory_read(raw) if inspect_graphics else False
        graphic_pages += int(graphics_read)
        format_checks = inspect_sheet_format(raw, title_block) if 8 in selected or 10 in selected else []
        if 8 in selected:
            a1 = any(check['field'] == 'template_code' and check['result'] == 'passed' for check in format_checks)
            for check in inspect_title_fields(raw, title_block, a1, graphics, graphics_read):
                title_field_checks.append(dict(page=i, **check))
                if check['result'] not in ('empty', 'blank_link'):
                    continue
                field = check['field']
                x0, y0, x1, y1 = check['cellBounds']
                box = [x0/sw, 1-y1/sh, x1/sw, 1-y0/sh]
                details = '属性链接存在，SolidWorks 未返回图面显示值。' if check['result'] == 'blank_link' else '单元格未见有效显示值。'
                if not check['required']:
                    details += '数量为补充核对项，请确认该图纸是否需要填写。'
                finding = issue(i, 8, f'明细栏{field}未填写', details, box,
                                severity='error' if check['required'] else 'warning')
                finding['relatedObjectIds'] = check['objectIds']
                findings.append(finding)
        if 9 in selected:
            cells = inspect_signature_cells(raw, title_block)
            if (len(cells) == len(ROLES)*2
                    and len({cell['cellBounds'] for cell in cells}) == len(cells)
                    and graphics_read and 'NoteMetadata:' in raw and 'NoteMetadataReadError:' not in raw):
                signature_pages_read += 1
            for graphic in graphics:
                nearby = [f'{cell["role"]}{cell["field"]}' for cell in cells
                          if graphic['bounds'] and overlaps(cell['cellBounds'], graphic['bounds'])]
                if nearby or graphic['bounds'] is None or not cells:
                    signature_graphics.append(dict(page=i, fields=nearby, result='unverified_graphic', **graphic))
            for cell in cells:
                x0, y0, x1, y1 = cell['cellBounds']
                box = [x0/sw, 1-y1/sh, x1/sw, 1-y0/sh]
                signature_checks.append(dict(page=i, box=box, **cell))
                if cell['result'] != 'filled':
                    continue
                role, field = cell['role'], cell['field']
                content = '、'.join(dict.fromkeys(entry['text'] for entry in cell['entries']))
                finding = issue(i, 9, f'{role}栏填写了{field}',
                                f'图纸在{role}{field}格显示“{content}”，该位置应留空。', box)
                finding['relatedObjectIds'] = sorted({entry['name'] for entry in cell['entries'] if entry['name']})
                findings.append(finding)
        if 11 in selected:
            marks = list(watermark_evidence(raw, sw, sh, graphics))
            watermark_checks.append(dict(page=i, graphicsRead=graphics_read, objects=marks))
            for mark in marks:
                if mark['result'] != 'controlled_stamp':
                    continue
                x0, y0, x1, y1 = mark['bounds']
                box = [max(0,x0/sw), max(0,1-y1/sh), min(1,x1/sw), min(1,1-y0/sh)]
                finding = issue(i, 11, '左上方有受控水印文字', '图纸左上方显示“大族数控受控文件”，请移除该标记。', box)
                finding['objectId'] = mark['objectId']
                findings.append(finding)
        seen = set()
        standards = set()
        for text, extent in notes:
            if (text, extent) in seen:
                continue
            seen.add((text, extent))
            try:
                nums = [float(x) for x in extent.split(' Linked:')[0].strip(', ').split(',')[:6]]
                bounds = (min(nums[0],nums[3]), min(nums[1],nums[4]), max(nums[0],nums[3]), max(nums[1],nums[4]))
                x0,y0,x1,y1 = bounds
                box = [max(0, x0/sw), max(0, 1-y1/sh), min(1, x1/sw), min(1, 1-y0/sh)]
                if box[2] <= box[0]: box[2] = min(1, box[0] + .03)
            except Exception:
                bounds = None
                box = [.6, .75, .95, .90]
            if 4 in selected:
                for wrong, right in [('Arail', 'Arial'), ('Mpa', 'MPa'), ('Kg', 'kg'), ('HZ', 'Hz'), ('N.M', 'N·m')]:
                    if re.search(r'(?<![A-Za-z])' + re.escape(wrong) + r'(?![A-Za-z])', text) or (wrong == 'N.M' and wrong in text):
                        findings.append(issue(i, 4, f'文字或单位格式：{wrong}', f'原文含“{wrong}”，请核对并改为“{right}”。', box))
            standard = re.search(r'(?:GB|JB)(?:/T)?\s*\d+(?:\.\d+)?(?:-\d{4})?', text)
            if 12 in selected and standard and standard[0] not in standards:
                standards.add(standard[0])
                findings.append(issue(i, 12, '引用标准版本待核验', '已识别标准引用，尚未连接标准有效性数据源；不能据此判定现行或废止。', box, severity='pending'))
            if 1 in selected and bounds and text.lstrip().startswith('技术要求'):
                if frame and (x0 < frame[0]-.0005 or y0 < frame[1]-.0005 or x1 > frame[2]+.0005 or y1 > frame[3]+.0005):
                    findings.append(issue(i, 1, '技术要求可能超出内图框', '文字范围超过模板内框线，请核对实际字形并调整位置。', box, severity='warning'))
                if title_block:
                    left,bottom,right,top = title_block
                    if min(x1,right)-max(x0,left) > .001 and min(y1,top)-max(y0,bottom) > .001:
                        findings.append(issue(i, 1, '技术要求与明细栏重叠', 'API文字范围与模板明细栏相交，请核对字形并调整文字位置。此项为版面附加检查。', box, severity='warning'))
        if 1 in selected and frame:
            left, bottom, right, top = frame
            for view in re.split(r'^View: ', raw, flags=re.M)[1:]:
                for dimension in re.split(r'^DIM: ', view, flags=re.M)[1:]:
                    points = [point for segment in display_segments(dimension) for point in segment]
                    if not points:
                        continue
                    x0,y0 = map(min,zip(*points))
                    x1,y1 = map(max,zip(*points))
                    margin = .0005
                    if x0 >= left-margin and x1 <= right+margin and y0 >= bottom-margin and y1 <= top+margin:
                        continue
                    box = [max(0,(x0-margin)/sw),max(0,1-(y1+margin)/sh),
                           min(1,(x1+margin)/sw),min(1,1-(y0-margin)/sh)]
                    if box[0] >= box[2] or box[1] >= box[3]:
                        continue
                    name = dimension.split(' | ',1)[0]
                    result = issue(i, 1, '尺寸显示线越过内图框',
                                   f'尺寸 {name.split("@")[0]} 的显示线端点超出模板内框。', box)
                    result.update(view=view.split(' Model: ',1)[0], objectId=name)
                    findings.append(result)
        if 5 in selected:
            for dimension in re.split(r'^DIM: ', raw, flags=re.M)[1:]:
                if 'Parentheses: False' not in dimension:
                    continue
                for label,x,y in re.findall(r'^RenderedDimText: (.*?) position=([^,]+),([^,]+),', dimension, re.M):
                    if not re.fullmatch(r'\s*\d+(?:\.\d+)?\s*[×xX*]\s*\d+(?:\.\d+)?\s*[=＝]\s*\d+(?:\.\d+)?\s*', label):
                        continue
                    u,v = float(x)/sw, 1-float(y)/sh
                    findings.append(issue(i, 5, '等距总尺寸缺少参考括号', f'图面“{label.strip()}”的总距离未加参考括号。按规则5，总距离应写在括号内。', [max(0,u-.008),max(0,v-.024),min(1,u+.15),min(1,v+.012)]))
        if 7 in selected:
            checks, chains = linear_chain_evidence(raw)
            chain_checks.extend(dict(page=i, **check) for check in checks)
            chain_count += len(chains)
            for chain in chains:
                total = chain['total']
                anchor, name, value = total['anchor'], total['objectId'], total['valueMm']
                u, v = anchor[0]/sw, 1-anchor[1]/sh
                if not (0 <= u <= 1 and 0 <= v <= 1):
                    continue
                dimensions = '、'.join(f'{part["objectId"].split("@")[0]}（{part["valueMm"]:g} mm）'
                                      for part in chain['segments'])
                result = issue(i, 7, '总尺寸与分段尺寸形成闭环',
                               f'{name.split("@")[0]}（{value:g} mm）的两个端点已由{dimensions}逐段相连。'
                               '请删除一条重复约束，或将其改为括号参考尺寸。',
                               [max(0,u-.04),max(0,v-.025),min(1,u+.04),min(1,v+.025)])
                result.update(view=chain['view'], objectId=name, evidence=chain,
                              relatedObjectIds=[part['objectId'] for part in chain['segments']])
                findings.append(result)
        if any(rule in selected for rule in (3,5,6)):
            for claim in repeated_hole_counts(raw):
                if 5 in selected and packet is not None:
                    dimensional_holes.append((i,claim))
                if 6 in selected and claim['result'] == 'matched_wizard_group':
                    wizard_size_checks.append(dict(page=i, **claim))
                    measured = claim.get('cylinderDepths', [])
                    if claim.get('depthMatchesModel') is False and measured and all(
                            abs(depth['cylinderDepthMm']-claim['depthMm']) > .01 for depth in measured):
                        x, y = claim['anchor']
                        u, v = x/sw, 1-y/sh
                        if 0 <= u <= 1 and 0 <= v <= 1:
                            settings_depth = claim['wizard']['parameters']['holeDepth']*1000
                            values = '、'.join(f'{depth:g}' for depth in sorted(
                                {rim['cylinderDepthMm'] for rim in measured}))
                            discrepancy = issue(i, 6, '孔深标注与模型不一致',
                                                f'图面标注深 {claim["depthMm"]:g} mm，孔向导设定深 {settings_depth:g} mm，'
                                                f'图面孔口对应圆柱壁的轴向长度为 {values} mm。请核对加工要求与模型。',
                                                [max(0,u-.04),max(0,v-.025),min(1,u+.04),min(1,v+.025)],
                                                severity='warning')
                            discrepancy.update(view=claim['view'], objectId=claim['objectId'], evidence=claim, status='problem')
                            findings.append(discrepancy)
                if 3 in selected:
                    hole_claims.append(dict(page=i, **claim))
                if 3 not in selected or claim['result'] != 'exceeds_bound':
                    continue
                anchor, quantity = claim['anchor'], claim['quantity']
                diameter, upper_bound = claim['diameterMm'], claim['modelUpperBound']
                u, v = anchor[0]/sw, 1-anchor[1]/sh
                if not (0 <= u <= 1 and 0 <= v <= 1):
                    continue
                result = issue(i, 3, '标注数量超过同径圆柱面的数量上限',
                               f'标注为 {quantity} 个 Ø{diameter:g} mm 孔，且已绑定一个同径孔口。'
                               f'对应零件配置的全部实体只有 {upper_bound} 个同径内圆柱面。',
                               [max(0,u-.04),max(0,v-.025),min(1,u+.04),min(1,v+.025)])
                result.update(view=claim['view'], objectId=claim['objectId'], evidence=claim)
                findings.append(result)
        if 2 in selected:
            for target in dimension_target_evidence(raw):
                dimension_targets.append(dict(page=i, **target))
                if target['result'] != 'dangling':
                    continue
                ax, ay = target['anchor']
                x, y = ax/sw, 1-ay/sh
                if not (0 <= x <= 1 and 0 <= y <= 1):
                    continue
                finding = issue(i, 2, '尺寸关联悬空', 'SolidWorks API 返回 IsDangling=True，请重新关联有效几何。',
                                [max(0,x-.03), max(0,y-.025), min(1,x+.03), min(1,y+.025)])
                finding.update(view=target['view'], objectId=target['objectId'], evidence=target)
                findings.append(finding)
        if 10 in selected:
            for check in format_checks:
                template_checks.append(dict(page=i, **check))
                for entry in check['entries']:
                    if entry['matches']:
                        continue
                    title, reason = {
                        'company': ('图框使用旧公司名称', f'模板显示“{entry["text"]}”，规定名称为“{COMPANY}”。'),
                        'template_code': ('图框模板编号不符', f'模板显示“{entry["text"]}”，规定编号为 {TEMPLATE}。'),
                        'process_field': ('图框明细栏含工艺栏', '图纸格式中仍有“工艺”栏标签，规定模板应移除该栏。'),
                    }[check['field']]
                    x0, y0, x1, y1 = entry['bounds']
                    box = [max(0,x0/sw), max(0,1-y1/sh), min(1,x1/sw), min(1,1-y0/sh)]
                    finding = issue(i, 10, title, reason, box)
                    finding['objectId'] = entry['name']
                    findings.append(finding)
    if packet is not None and dimension_rules:
        severities = {'problem':'error','pass':'warning','missing':'pending'}
        for row in constraint_findings(packet, dimension_rules, dimensional_holes):
            findings.append(dict(row, method='规则引擎', severity=severities[row['status']]))
            chain_count += row['rule']==7
    for r in selected:
        if r == 2 and dimension_targets:
            states = {state: sum(target['result'] == state for target in dimension_targets) for state in
                      ('cad_geometry','thread_geometry','visible_round_target','visible_witness_target',
                       'auxiliary_geometry','unresolved','dangling','coincident_dimensions')}
            coverage.append(dict(rule=2, status='partial', checks=dimension_targets,
                                 reason=f'{states["cad_geometry"]} 条尺寸的关联均指向 CAD 几何，'
                                        f'{states["thread_geometry"]} 条尺寸的装饰螺纹符号有圆边几何支撑，'
                                        f'{states["visible_round_target"]} 条的箭头匹配到可见圆边，'
                                        f'{states["visible_witness_target"]} 条的尺寸界线匹配到可见直边，'
                                        f'{states["auxiliary_geometry"]} 条含辅助几何，'
                                        f'{states["unresolved"]} 条关联待核验，'
                                        f'确认 {states["dangling"]} 条悬空。'
                                        f'{states["coincident_dimensions"]} 组尺寸的关联和显示内容相同。'))
            continue
        if r == 7 and chain_checks:
            verified = sum(check['result'] == 'verified_span' for check in chain_checks)
            references = sum(check['result'] == 'reference_excluded' for check in chain_checks)
            coverage.append(dict(rule=7, status='partial', checks=chain_checks,
                                 reason=f'{verified} 条直线尺寸核对了参考点和界线，检出 {chain_count} 个闭环。'
                                        f'{references} 条括号参考尺寸不参与闭环，'
                                        f'{len(chain_checks)-verified-references} 条直线尺寸缺少可确认的端点证据。'))
            continue
        if r == 8:
            required = [check for check in title_field_checks if check['required']]
            present = sum(check['result'].startswith('present_') for check in required)
            pending = sum(check['result'] == 'pending' for check in required)
            supplemental = sum(check['result'] in ('empty', 'blank_link') for check in title_field_checks if not check['required'])
            status = 'pending' if pending == len(required) else 'partial' if pending else 'checked'
            coverage.append(dict(rule=8, status=status, checks=title_field_checks,
                                 reason=f'{present}/{len(required)} 个明确必填格有可见值，'
                                        f'{len(required)-present-pending} 格缺填，{pending} 格待核对。'
                                        f'另有 {supplemental} 个数量格缺填，列作补充核对。'))
            continue
        if r == 11:
            marks = [mark for page in watermark_checks for mark in page['objects']]
            confirmed = sum(mark['result'] == 'controlled_stamp' for mark in marks)
            coverage.append(dict(rule=11, status='partial', checks=watermark_checks,
                                 reason=f'左上区域检出 {confirmed} 处可见受控水印文字，'
                                        f'{len(marks)-confirmed} 个对象待核验。'
                                        f'{graphic_pages}/{len(pages)} 页读取了图片、块和 OLE 清单。'))
            continue
        if r == 10:
            counts = {state: sum(check['result'] == state for check in template_checks)
                      for state in ('passed', 'failed', 'pending')}
            status = 'partial' if counts['pending'] else 'checked'
            if counts['pending'] == len(template_checks):
                status = 'pending'
            coverage.append(dict(rule=10, status=status, checks=template_checks,
                                 reason=f'逐页核对公司名称、模板编号和工艺栏。'
                                        f'{counts["passed"]} 项符合，{counts["failed"]} 项不符，'
                                        f'{counts["pending"]} 项缺少可判定证据。'))
            continue
        if r == 9 and (signature_checks or signature_graphics):
            expected = len(pages)*len(ROLES)*2
            filled = sum(cell['result'] == 'filled' for cell in signature_checks)
            uncertain = sum(cell['result'] == 'unresolved' for cell in signature_checks)
            complete = (signature_pages_read == len(pages) and len(signature_checks) == expected
                        and not uncertain and not signature_graphics)
            coverage.append(dict(rule=9, status='checked' if complete else 'partial',
                                 checks=signature_checks+signature_graphics,
                                 reason=f'定位 {len(signature_checks)}/{expected} 个签名和日期格。'
                                        f'{filled} 格含可见文字，{uncertain} 格的文字或可见性待核验。'
                                        f'{len(signature_graphics)} 个可能涉及签名区域的图形对象待核验。'
                                        f'{graphic_pages}/{len(pages)} 页读取了图形对象清单。'))
            continue
        if r == 5 and location_checks:
            located = sum(check['result'] == 'located_from_model_edge' for check in location_checks)
            relative = len(location_checks)-located
            coverage.append(dict(rule=5, status='partial', checks=location_checks,
                                 reason=f'确认 {located} 条腰孔单方向定位可追溯至模型边，'
                                        f'{relative} 条单方向证据只涉及槽间距。其他方向和特征仍待核对。'))
            continue
        if r == 3 and (hole_claims or slot_count_checks):
            matched = sum(check['result'] in ('matched_visible_set', 'matched_base_visible_set')
                          for check in slot_count_checks)
            exact = sum(claim['result'] == 'matched_visible_feature_group' for claim in hole_claims)
            patterns = sum(claim['result'] == 'matched_pattern_group' for claim in hole_claims)
            wizards = sum(claim['result'] == 'matched_wizard_group' for claim in hole_claims)
            bound = sum(claim['result'] in ('within_bound','exceeds_bound') for claim in hole_claims)
            coverage.append(dict(rule=3, status='partial', checks=hole_claims+slot_count_checks,
                                 reason=f'{exact} 条圆孔标注与同一建模特征的可见孔组数量一致，'
                                        f'{patterns} 条与 CAD 阵列及种子特征的孔组数量一致，'
                                        f'{wizards} 条与孔向导特征的可见孔位数量一致，'
                                        f'{bound} 条仅校核数量上限，'
                                        f'{len(hole_claims)-exact-patterns-wizards-bound} 条孔类标注缺少分组或绑定证据。'
                                        f'{matched}/{len(slot_count_checks)} 组腰孔标注与所在视图或父视图的同规格实体数量一致。'
                                        '其他分组及特征类型仍待核对。'))
            continue
        if r == 6 and (slot_dimension_checks or wizard_size_checks):
            coverage.append(dict(rule=6, status='partial', checks=slot_dimension_checks+wizard_size_checks,
                                 reason=f'确认 {len(slot_dimension_checks)} 组腰孔的宽度和总长均有尺寸，'
                                        f'另有 {len(wizard_size_checks)} 条孔标注可对应孔向导规格和孔位。'
                                        '其他实体及未绑定尺寸仍待核对。'))
            continue
        covered = r in [1, 4, 5, 12]
        coverage.append(dict(rule=r, status='partial' if covered else 'pending', reason='已执行可验证的字段检查。其余情况需视觉或人工复核。' if covered else '需视觉或工程师判断，未按未检出自动判合格。'))
    return findings, coverage


def codex_binary():
    configured=os.environ.get('CODEX_CLI_PATH')
    if configured:return configured if pathlib.Path(configured).is_file() else None
    found=shutil.which('codex')
    if found:return found
    bins=pathlib.Path(os.environ.get('LOCALAPPDATA',''))/'OpenAI/Codex/bin'
    matches=list(bins.glob('*/codex.exe'))
    return str(max(matches,key=lambda p:p.stat().st_mtime)) if matches else None


def model_settings():
    path = DATA / 'model-settings.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'provider': 'codex-cli'}


def public_model_settings(settings):
    return {key: settings.get(key, '') for key in ('provider', 'baseUrl', 'model')} | {
        'hasApiKey': bool(settings.get('apiKey'))
    }


def save_model_settings(payload):
    provider = payload.get('provider')
    if provider not in ('codex-cli', 'custom'):
        raise ValueError('请选择有效的模型来源。')
    with SETTINGS_LOCK:
        settings = model_settings()
        if provider == 'custom':
            base_url = payload.get('baseUrl', '').strip().rstrip('/')
            endpoint = urlparse(base_url)
            if (endpoint.scheme not in ('https', 'http') or not endpoint.hostname
                    or endpoint.username is not None or endpoint.query or endpoint.fragment
                    or endpoint.params or any(c.isspace() for c in base_url)):
                raise ValueError('请填写有效的 HTTP(S) API 基础地址。')
            if endpoint.port is not None and endpoint.port == 0:
                raise ValueError('请填写有效的服务端口。')
            model = payload.get('model', '').strip()
            if not model or len(model) > 256:
                raise ValueError('请填写模型名称。')
            api_key = payload.get('apiKey', '').strip()
            if any(not 33 <= ord(c) <= 126 for c in api_key):
                raise ValueError('API 密钥包含无效字符。')
            if not api_key and not payload.get('clearApiKey') and base_url == settings.get('baseUrl'):
                api_key = settings.get('apiKey', '')
            settings.update(baseUrl=base_url, model=model, apiKey=api_key)
        settings['provider'] = provider
        temporary = DATA / 'model-settings.tmp'
        temporary.write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
        temporary.replace(DATA / 'model-settings.json')
    return public_model_settings(settings)


def custom_vision(settings, prompt, images, packet, raw_pages=None):
    endpoint = urlparse(settings['baseUrl'])
    path = endpoint.path.rstrip('/')
    if not path.endswith('/chat/completions'):
        path += '/chat/completions'
    content = [{'type': 'text', 'text': prompt}]
    for image in images:
        encoded = base64.b64encode(image.read_bytes()).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + encoded}})
    messages = [{'role': 'user', 'content': content}]
    tools = [{'type':'function','function':{'name':tool['name'],'description':tool['description'],
                                           'parameters':tool['inputSchema']}} for tool in DRAWING_TOOLS]
    headers = {'Content-Type': 'application/json'}
    if settings.get('apiKey'):
        headers['Authorization'] = 'Bearer ' + settings['apiKey']
    connection_type = http.client.HTTPSConnection if endpoint.scheme == 'https' else http.client.HTTPConnection
    tool_reads = 0
    read_pages = set()
    for turn in range(13):
        payload = {'model': settings['model'], 'messages': messages, 'tools': tools,
                   'tool_choice': 'none' if turn == 12 else 'auto'}
        if settings['model'].casefold().rsplit('/', 1)[-1] == 'kimi-k3':
            payload.update(thinking={'type':'disabled'}, max_tokens=8192)
        body = request_model_turn(connection_type, endpoint, path, headers, payload, turn+1)
        if len(body) > 2 * 1024 * 1024:
            raise RuntimeError('自定义模型响应超过大小限制。')
        try:
            message = json.loads(body)['choices'][0]['message']
            calls = message.get('tool_calls') or []
            if not calls:
                require_evidence_pages(read_pages, len(packet['pages']))
                answer = re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '', message['content'].strip())
                return json.loads(answer)
            assistant = {'role':'assistant', 'content':message.get('content'), 'tool_calls':calls}
            if message.get('reasoning_content'):
                assistant['reasoning_content'] = message['reasoning_content']
            messages.append(assistant)
            for call in calls:
                function = call['function']
                args = json.loads(function['arguments'])
                if tool_reads < 48:
                    tool_name = function['name']
                    data = call_drawing_tool(packet,tool_name,args,raw_pages)
                    if 'error' not in data:
                        tool_reads += 1
                        if tool_name==EVIDENCE_TOOL['name']:
                            read_pages.add(args['page'])
                else:
                    data = {'error': '无效工具请求，或已达到证据查询上限。'}
                messages.append({'role':'tool', 'tool_call_id':call['id'],
                                 'content':json.dumps(data, ensure_ascii=False)})
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise RuntimeError('自定义模型未返回有效的工具调用或审核 JSON。') from None
    raise RuntimeError('自定义模型超过证据查询轮次，未返回审核结论。')


def request_model_turn(connection_type, endpoint, path, headers, payload, turn):
    encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    for attempt in range(2):
        connection = connection_type(endpoint.hostname, endpoint.port, timeout=180)
        stage = '发送请求'
        try:
            connection.request('POST', path, encoded, headers)
            stage = '等待模型响应'
            response = connection.getresponse()
            if response.status != 200:
                if attempt == 0 and response.status in (502, 503, 504):
                    continue
                raise RuntimeError(f'自定义模型服务第 {turn} 轮返回 HTTP {response.status}。')
            stage = '读取模型响应'
            return response.read(2 * 1024 * 1024 + 1)
        except TimeoutError:
            if attempt:
                raise RuntimeError(f'自定义模型第 {turn} 轮{stage}超时，重试后仍未完成。') from None
        except (ConnectionError, http.client.RemoteDisconnected):
            if attempt:
                raise RuntimeError(f'自定义模型第 {turn} 轮{stage}时连接断开，重试后仍失败。') from None
        except (OSError, http.client.HTTPException) as error:
            raise RuntimeError(f'自定义模型第 {turn} 轮{stage}失败（{type(error).__name__}）。') from None
        finally:
            connection.close()


def valid_box(box):
    return (isinstance(box, list) and len(box) == 4
            and all(type(v) in (int,float) and 0 <= v <= 1 for v in box)
            and box[0] < box[2] and box[1] < box[3])


def evidence_marker(element, rect):
    if valid_box(element.get('box')):
        return element['box']
    points = element.get('pdfBoxPt')
    if points is None and element.get('pdfAnchorPt'):
        points = element['pdfAnchorPt'] * 2
    if points is None:
        return None
    x0,y0,x1,y1 = points
    if x1 < 0 or y1 < 0 or x0 > rect.width or y0 > rect.height:
        return None
    box = [max(0,(x0-3)/rect.width),max(0,(y0-3)/rect.height),
           min(1,(x1+3)/rect.width),min(1,(y1+3)/rect.height)]
    return box if valid_box(box) else None


def require_evidence_pages(read_pages, page_count):
    missing = sorted(set(range(1,page_count+1))-read_pages)
    if missing:
        raise RuntimeError('审图 Agent 未读取以下 PDF 页的 API 证据：'+ '、'.join(map(str,missing))+'。')


def vision(pdf, selected, workdir=None, evidence=None, settings=None, *, audit=None, raw_pages=None):
    settings = settings if settings is not None else model_settings()
    custom = settings['provider'] == 'custom'
    binary = None if custom else codex_binary()
    if not custom and not binary:raise RuntimeError('未找到 Codex CLI，请安装并登录后重试。')
    temporary=tempfile.TemporaryDirectory() if workdir is None else None
    workdir=pathlib.Path(temporary.name if temporary else workdir)
    workdir.mkdir(parents=True,exist_ok=True)
    try:
        images=[]
        for i,p in enumerate(pdf,1):
            scale=min(2.5,2300/p.rect.width)
            path=workdir/f'page-{i}.png'
            p.get_pixmap(matrix=fitz.Matrix(scale,scale)).save(path)
            images.append(path)
        if not images:return [],'没有可审核的图纸页。'
        packet = evidence if isinstance(evidence, dict) else build_evidence(pdf, evidence or [], '', template_regions)
        manifest = [dict(page=row['page'], sheet=row['sheet'], calibration=row['calibration'],
                         elementCounts=row['elementCounts']) for row in packet['pages']]
        final = workdir/'result.json'
        validated = workdir/'validated-result.json'
        final.unlink(missing_ok=True)
        validated.unlink(missing_ok=True)
        prompt=(
            '你是机械工程图初审员。这里按顺序附上同一份工程图的全部'+str(len(images))+'页，务必跨页、跨视图核对后再判断漏标。'
            '只检查所选规则编号'+str(selected)+'。'
            '规则1：所有标注应在有效图框内（不把模板固定页脚误判越框）；'
            '2：标注引线需指向真实有效特征，未悬空也可能多余；'
            '3至7：按下方审图技能中的证据核验及对应规则执行。'
            '同一规则有已证实的局部通过项和疑点时，分别输出可定位记录。'
            'R02 复用已核实的模型引线关联，剩余疑点指出具体引线和候选目标。'
            'R07 人工复核须指出具体候选尺寸链及未核实的端点，避免笼统报告密集区域待核对。'
            'R05 报缺项前，沿尺寸界线双向追踪至任一有效基准。R07 先核实各段方向和实际端点，再判断算式及闭环。'
            '输出前核对孔组的计数范围、尺寸符号对应的几何量、定位基准、折弯各段及闭环算式。'
            '每个缺项定位到具体对象，合并重复发现。'
            '8：材料、机型、零件名称和图号不能空缺，HANS也可能是实际机型属性值，不可当作空白；'
            '9：设计、校对、标准化、审核、批准区域不得有签名和日期；'
            '10：新模板不含工艺栏，公司名为深圳市大族数控科技股份有限公司，编号CQR-07-001-2020-07-02-A1；'
            '11：左上方不得有受控文件印章，底部机密提示不是受控章；'
            '12：识别标准引用。缺少权威版本数据时，在 ruleReviews 记录一次 missing。'
            '\n本次运行使用以下审图技能。\n'
            +(ROOT/'skills/drawing-review/SKILL.md').read_text(encoding='utf-8')
            +'\nPDF 页及证据摘要\n'+json.dumps(manifest,ensure_ascii=False)
            +'\n3D 模型摘要（按 modelKey 对应工程图视图）\n'+json.dumps(model_summaries(packet),ensure_ascii=False,separators=(',', ':'))
            +'\n图纸页与 API 页关联状态 '+packet['pageAlignment'])
        started=time.time()
        if custom:
            schema = (ROOT/'vision-schema.json').read_text(encoding='utf-8')
            parsed = custom_vision(settings, prompt + '\n只返回 JSON 对象，结构如下：\n' + schema, images, packet, raw_pages)
            method = '自定义模型审图 Agent'
        else:
            evidence_path = (workdir/'evidence.json').resolve()
            receipt = (workdir/'evidence-reads.jsonl').resolve()
            receipt.unlink(missing_ok=True)
            write_json(evidence_path, packet)
            tool_args = [str(ROOT/'drawing_evidence_tool.py'), str(evidence_path), str(receipt)]
            if raw_pages is not None and any(rule in selected for rule in (3,5,6)):
                raw_path = (workdir/'solidworks-raw.json').resolve()
                write_json(raw_path,raw_pages)
                tool_args.append(str(raw_path))
            args=[binary,'exec','--skip-git-repo-check','--ephemeral','--sandbox','read-only','--color','never',
              '--output-schema',str(ROOT/'vision-schema.json'),'--output-last-message',str(final),'-C',str(workdir),
              '-c','mcp_servers.drawing_evidence.command='+json.dumps(sys.executable,ensure_ascii=False),
              '-c','mcp_servers.drawing_evidence.args='+json.dumps(tool_args,ensure_ascii=False),
              '-c','mcp_servers.drawing_evidence.tools.read_drawing_evidence.approval_mode="approve"',
              '-c','mcp_servers.drawing_evidence.tools.2D_vs_3D_Diff.approval_mode="approve"',
              '-c','mcp_servers.drawing_evidence.tools.check_dimension_constraints.approval_mode="approve"']
            model=os.environ.get('CODEX_VISION_MODEL')
            if model:args+=['--model',model]
            for image in images:args+=['--image',str(image)]
            args+=['--','-']
            (workdir/'prompt.txt').write_text(prompt,encoding='utf-8')
            with VISION_LOCK:
                result=subprocess.run(args,input=prompt.encode('utf-8'),capture_output=True,timeout=900,cwd=workdir)
            (workdir/'cli.stdout.log').write_bytes(result.stdout)
            (workdir/'cli.stderr.log').write_bytes(result.stderr)
            if result.returncode or not final.exists():
                tail=result.stderr.decode('utf-8',errors='replace')[-1200:]
                raise RuntimeError('Codex CLI审核失败：'+tail)
            if audit is not None:
                reads = map(json.loads,receipt.read_text(encoding='utf-8').splitlines()) if receipt.exists() else ()
                read_pages = {entry['page'] for entry in reads if entry.get('tool',EVIDENCE_TOOL['name'])==EVIDENCE_TOOL['name']}
                require_evidence_pages(read_pages, len(pdf))
            parsed=json.loads(final.read_text(encoding='utf-8-sig'))
            method = 'Codex CLI 审图 Agent'
        if not isinstance(parsed, dict) or not isinstance(parsed.get('issues'), list):
            raise RuntimeError('视觉模型响应缺少有效的问题列表。')
        statuses = {'problem':'error', 'pass':'warning', 'manual':'warning', 'missing':'pending'}
        summaries = {}
        summary_rows = parsed.get('ruleReviews')
        for row in summary_rows if isinstance(summary_rows, list) else []:
            if (isinstance(row, dict) and type(row.get('rule')) is int and row['rule'] in selected
                    and isinstance(row.get('status'), str) and row['status'] in statuses
                    and isinstance(row.get('reason'), str) and row['reason'].strip()):
                if row['rule'] in summaries:
                    raise RuntimeError(f'审图 Agent 重复返回规则 R{row["rule"]:02d}。')
                summaries[row['rule']] = dict(rule=row['rule'], status=row['status'], reason=row['reason'].strip()[:2000])
        by_page = [{element['id']: element for element in row['elements']} for row in packet['pages']]
        issues=[]
        for finding_index, a in enumerate(parsed.get('issues', []), 1):
            if not isinstance(a, dict):continue
            page, rule = a.get('page'), a.get('rule')
            if type(page)!=int or not 1<=page<=len(pdf) or type(rule) is not int or rule not in selected:continue
            verdict = a.get('status', 'manual')
            if not isinstance(verdict, str) or verdict not in statuses:continue
            refs = a.get('elementIds')
            refs = list(dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref in by_page[page-1])) if isinstance(refs, list) else []
            located = [(ref,box) for ref in refs
                       if (box := evidence_marker(by_page[page-1][ref],pdf[page-1].rect))]
            image_box = a.get('box')
            if not valid_box(image_box) and located:
                positions = [(box, 'api', [ref]) for ref,box in located]
            elif valid_box(image_box):
                matches = [(ref,box) for ref,box in located if overlaps(box,image_box)]
                if matches and len(matches) == len(refs):
                    positions = [(box, 'api', [ref]) for ref,box in matches]
                else:
                    positions = [(image_box, 'image', refs)]
                    positions.extend((box,'api',[ref]) for ref,box in located
                                     if not overlaps(box,image_box))
            else:
                if audit is None or image_box not in ([], None):
                    continue
                views = {by_page[page-1][ref]['view'] for ref in refs if by_page[page-1][ref]['view']}
                outline = next((e['box'] for e in by_page[page-1].values()
                                if len(views)==1 and e['kind']=='view' and e['view'] in views and valid_box(e['box'])), None)
                positions = [(outline if verdict in ('manual','missing') else None, 'view' if outline and verdict in ('manual','missing') else None, refs)]
            for box, basis, element_ids in positions:
                entry = issue(page,rule,str(a.get('title','审图 Agent 意见'))[:150],str(a.get('reason',''))[:2000],
                              box,method=method,severity=statuses[verdict])
                issues.append(dict(entry, status=verdict, elementIds=element_ids,
                                   citedElementIds=refs, positionSource=basis, findingId=finding_index))
        if 6 in selected:
            confirmed = [check for page in range(1, len(pdf)+1) for check in drawing_model_diff(packet,page)['checks']
                         if check['status'] == 'problem']
            confirmed_keys = {(check['page'], check['elementIds'][0]) for check in confirmed}
            issues = [entry for entry in issues if entry['rule'] != 6 or
                      not any((entry['page'], ref) in confirmed_keys for ref in entry['elementIds'])]
            for finding_index, check in enumerate(confirmed, len(parsed.get('issues', []))+1):
                refs = check['elementIds']
                entry = issue(check['page'],6,check['title'],check['reason'],check['box'],
                              method='2D_vs_3D_Diff',severity='error')
                issues.append(dict(entry,status='problem',elementIds=refs,citedElementIds=refs,
                                   positionSource='api',findingId=finding_index))
            if confirmed:
                summaries[6] = dict(rule=6,status='problem',reason=f'确定性比对发现 {len(confirmed)} 处 2D 与 3D 尺寸不一致。')
        rule_reviews = [summaries[rule] for rule in dict.fromkeys(selected) if rule in summaries]
        problems, manual_notes, published_reviews = publish_results(issues, rule_reviews)
        write_json(validated, {'issues': problems, 'manualNotes': manual_notes, 'ruleReviews': published_reviews})
        if audit is not None:
            audit['ruleReviews'] = rule_reviews
        model_label = settings['model'] if custom else 'Codex CLI'
        return issues,f'{model_label} 已审核 {len(pdf)} 页，耗时 {int(time.time()-started)} 秒。'
    finally:
        if temporary:temporary.cleanup()


def review(j, rules, settings=None, *, reuse_visual=False):
    try:
        settings = settings if settings is not None else model_settings()
        base = DATA / j['id']
        ready = [x for x in j['files'] if x['status']=='ready']
        for idx, item in enumerate(ready):
            save(j, message=f'正在审核 {item["name"]}', progress=int(idx/len(ready)*100))
            pages = drawing_pages(base,item)
            audit = {}
            with fitz.open(base / f'{item["id"]}.pdf') as d:
                packet = build_evidence(d, pages, item['name'], template_regions)
                write_json(base/'evidence'/f'{item["id"]}.json', packet)
                findings = []
                if reuse_visual:
                    cached = item.get('issues',[]) + item.get('manualNotes',[])
                    cached += [check for row in item.get('ruleReviews',[]) for check in row.get('passedChecks',[])]
                    findings = [a for a in cached if a.get('method','').endswith(('视觉审核','审图 Agent')) and a['rule'] in rules]
                    audit['ruleReviews'] = [row for row in item.get('agentRuleReviews',[]) if row['rule'] in rules]
                    status = item.get('vision','视觉审核尚未执行。')
                else:
                    try:
                        findings, status = vision(d, rules, base/'vision'/item['id'], packet, settings, audit=audit, raw_pages=pages)
                    except Exception as e:
                        status = '审图 Agent 调用失败：'+str(e)
                agent_rules = {row['rule'] for row in audit.get('ruleReviews',[])}
                missing_rules = set(rules) - agent_rules
                agent_complete = not missing_rules
                coverage = []
                if agent_rules and 7 in rules:
                    confirmed = [row for row in constraint_findings(packet,{7},[],include_same_view=True)
                                 if row['status']=='problem']
                    for row in findings:
                        if row['rule']==7 and row.get('status')=='problem':
                            # An image region can overlap unrelated dimensions. Require
                            # the verified total dimension and every segment by ID.
                            ids = set(row.get('citedElementIds',row.get('elementIds',[])))
                            supported = any(check['page']==row['page'] and
                                            all(part['page']==row['page'] for part in check['evidence']['dimensions']) and
                                            {check['objectId'],*(part['id'] for part in check['evidence']['dimensions'])} <= ids
                                            for check in confirmed)
                            if not supported:
                                row.update(status='manual',severity='warning',
                                           reason='程序未核实完整的独立尺寸链，请复核端点与算式。Agent 线索：'+row['reason'])
                    for row in audit['ruleReviews']:
                        if row['rule']==7 and row['status']=='problem' and not any(
                                finding['rule']==7 and finding.get('status')=='problem' for finding in findings):
                            row.update(status='manual',reason='程序未核实完整的独立尺寸链，请复核端点与算式。')
                if not agent_complete:
                    fallback, coverage = engine(base, item, sorted(missing_rules), pages=pages, packet=packet)
                    covered = {(row['rule'],row['page'],row['title']) for row in findings}
                    findings.extend(row for row in fallback if (row['rule'],row['page'],row['title']) not in covered)
                    labels = '、'.join(f'R{rule:02d}' for rule in sorted(missing_rules))
                    status += f' {labels} 缺少 Agent 综合结论，已补充本地规则结果。'
                findings = [row for row in findings
                            if not (row['rule'] == 12 and row.get('status', row.get('severity')) in ('missing', 'manual', 'pending', 'warning'))
                            and not (row.get('positionSource') in ('image', 'view', None)
                                     and row.get('status') in ('manual', 'missing')
                                     and 'Agent' in row.get('method', ''))]
                for summary in audit.get('ruleReviews', []):
                    if summary['rule'] == 12:
                        summary.update(status='missing', reason='标准版本核验缺少权威数据源。')
                for summary in coverage:
                    if summary['rule'] == 12:
                        summary.update(status='pending', reason='标准版本核验缺少权威数据源。')
                if 6 in rules and (6 in missing_rules or reuse_visual):
                    for page in range(1, len(d)+1):
                        for check in drawing_model_diff(packet, page)['checks']:
                            if check['status'] == 'problem':
                                entry = issue(page,6,check['title'],check['reason'],check['box'],method='2D_vs_3D_Diff')
                                if not any(row['method'] == entry['method'] and row['page'] == page
                                           and row.get('elementIds') == check['elementIds'] for row in findings):
                                    findings.append(dict(entry,status='problem',elementIds=check['elementIds'],
                                                         positionSource='api'))
                results = build_review_results(findings, coverage, pages, item['id'], packet)
                issues, manual_notes, rule_reviews = publish_results(results, audit.get('ruleReviews', []))
                for a in drawable_results(issues):
                    if not 1 <= a['page'] <= len(d):
                        continue
                    b = a['box']
                    p = d[a['page']-1]
                    rect = fitz.Rect(b[0]*p.rect.width,b[1]*p.rect.height,b[2]*p.rect.width,b[3]*p.rect.height)
                    color = COLORS[a['status']]
                    if rect.is_empty:
                        continue
                    note=p.add_rect_annot(rect * p.derotation_matrix)
                    note.set_info(title=f'R{a["rule"]} · {a["method"]}',content=a['title']+'\n'+a['reason'])
                    note.set_border(width=1)
                    note.set_colors(stroke=color)
                    note.set_flags(note.flags | fitz.PDF_ANNOT_IS_PRINT)
                    note.update()
                target = base / f'{item["id"]}-review.pdf'
                temporary = target.with_suffix('.tmp')
                d.save(temporary, deflate=True)
                temporary.replace(target)
            item.pop('reviewResults', None)
            item.update(issues=issues, manualNotes=manual_notes, ruleReviews=rule_reviews, coverage=coverage,
                        agentRuleReviews=audit.get('ruleReviews',[]),
                        reviewSource='agent' if agent_complete else 'mixed' if agent_rules or any('Agent' in row['method'] for row in findings) else 'fallback',
                        vision=status, reviewed=True)
            save(j)
        bundle = base / 'review.zip'
        with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
            for item in ready:
                name = pathlib.Path(item['name']).stem+'-'+item['id']
                z.write(base / f'{item["id"]}-review.pdf', name+'-初审.pdf')
                z.write(base / 'evidence' / f'{item["id"]}.json', name+'-证据.json')
            z.writestr('问题清单.json',json.dumps(ready,ensure_ascii=False,indent=2))
        save(j,status='completed',progress=100,message='初审完成。请查看问题、人工审核项和缺少判断依据的项目。')
    except Exception as e:
        save(j,status='failed',error=str(e),message='审核失败')


class Handler(BaseHTTPRequestHandler):
    def send(self, data, status=200, mime='application/json; charset=utf-8'):
        if not isinstance(data,bytes):
            data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            path=urlparse(self.path).path
            if path=='/api/config':
                settings = model_settings()
                custom = settings['provider'] == 'custom'
                return self.send(dict(visionConfigured=custom or bool(codex_binary()), visionProvider=settings['provider'], visionModel=settings['model'] if custom else os.environ.get('CODEX_VISION_MODEL','Codex CLI 默认模型'), maxUploadMB=500))
            if path=='/api/model-settings':return self.send(public_model_settings(model_settings()))
            if path=='/api/jobs':return self.send(sorted(JOBS.values(),key=lambda j:j['created'],reverse=True))
            if path.startswith('/api/jobs/'):
                parts=path.split('/');j=JOBS.get(parts[3])
                if not j:return self.send({'error':'任务不存在'},404)
                if len(parts)==4:return self.send(j)
                base=DATA/j['id']
                if parts[4]=='bundle':return self.send((base/'review.zip').read_bytes(),mime='application/zip')
                item=next((x for x in j['files'] if x['id']==parts[4]),None)
                if not item:return self.send({'error':'图纸不存在'},404)
                if parts[5]=='log':
                    log = base/f'{item["id"]}.log'
                    data = log.read_bytes() if log.is_file() else (item.get('error') or '暂无转换日志。').encode('utf-8')
                    return self.send(data,mime='text/plain')
                if parts[5]=='evidence':
                    if item['status'] != 'ready':
                        return self.send({'error':'该图纸尚未转换完成'},409)
                    target = base/'evidence'/f'{item["id"]}.json'
                    if not target.exists():
                        with fitz.open(base/f'{item["id"]}.pdf') as drawing:
                            write_json(target, build_evidence(drawing, drawing_pages(base,item), item['name'], template_regions))
                    return self.send(target.read_bytes())
                reviewed=parse_qs(urlparse(self.path).query).get('review')==['1'] and item.get('reviewed')
                pdf=base/(item['id']+('-review' if reviewed else '')+'.pdf')
                if parts[5]=='pdf':return self.send(pdf.read_bytes(),mime='application/pdf')
                if parts[5]=='page':
                    page=int(parse_qs(urlparse(self.path).query).get('n',['1'])[0])-1
                    with fitz.open(pdf) as d:
                        if page<0 or page>=len(d):return self.send({'error':'页码越界'},400)
                        p=d[page]; scale=min(2,2000/p.rect.width)
                        pix=p.get_pixmap(matrix=fitz.Matrix(scale,scale))
                        return self.send(pix.tobytes('png'),mime='image/png')
                return self.send({'error':'资源不存在'},404)
            assets={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
            if path in assets:
                mime={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[assets[path].split('.')[-1]]
                return self.send((ROOT/'web'/assets[path]).read_bytes(),mime=mime)
            self.send({'error':'不存在'},404)
        except Exception as e:self.send({'error':str(e)},400)

    def do_POST(self):
        try:
            # Same-origin local service: block cross-site POSTs.
            origin=self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                return self.send({'error':'不允许跨来源请求'},403)
            path=urlparse(self.path).path
            if path=='/api/model-settings':
                length = int(self.headers.get('Content-Length', '0'))
                if self.headers.get_content_type() != 'application/json' or not 0 < length <= 16384:
                    return self.send({'error':'模型设置请求格式无效'},400)
                return self.send(save_model_settings(json.loads(self.rfile.read(length))))
            if path.endswith('/retry'):
                ident=path.split('/')[3];j=JOBS[ident]
                if j['status'] not in ('completed','awaiting_confirmation','failed'):
                    return self.send({'error':'当前任务正在处理'},409)
                if not any(f['status'] != 'ready' for f in j['files']):
                    return self.send({'error':'没有需要重试的文件'},409)
                for item in j['files']:
                    for key in ('issues','reviewResults','manualNotes','ruleReviews','agentRuleReviews','coverage','vision','reviewed'):item.pop(key,None)
                save(j,status='queued',progress=0,error=None,message='重试失败图纸，保留已成功转换的文件。')
                threading.Thread(target=convert,args=(j,True),daemon=True).start()
                return self.send(j)
            if path=='/api/jobs':
                length=int(self.headers.get('Content-Length','0'))
                if length<=0 or length>500*1024**2:return self.send({'error':'压缩包应为1字节至500MB'},413)
                ident=uuid.uuid4().hex; base=DATA/ident;base.mkdir()
                with (base/'upload.zip').open('wb') as f:
                    left=length
                    while left:
                        chunk=self.rfile.read(min(left,1024*1024))
                        if not chunk:raise ValueError('上传中断')
                        f.write(chunk);left-=len(chunk)
                if not zipfile.is_zipfile(base/'upload.zip'):raise ValueError('请上传有效ZIP压缩包')
                j=dict(id=ident,created=time.time(),name=self.headers.get('X-File-Name','图纸压缩包'),status='queued',progress=0,message='上传完成',files=[])
                JOBS[ident]=j;save(j)
                threading.Thread(target=convert,args=(j,),daemon=True).start()
                return self.send(j,201)
            if path.endswith('/review'):
                ident=path.split('/')[3];j=JOBS[ident]
                if j['status'] not in ('awaiting_confirmation','completed'):return self.send({'error':'请等待转换完成再开始审核'},409)
                payload=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
                rules=payload.get('rules',list(range(1,13)))
                if not rules or any(type(r)!=int or r not in range(1,13) for r in rules):raise ValueError('请选择有效审核规则')
                settings = model_settings()
                save(j,status='reviewing',progress=0,message='用户已确认，开始审图',selectedRules=rules)
                threading.Thread(target=review,args=(j,rules,settings),daemon=True).start();return self.send(j)
            self.send({'error':'不存在'},404)
        except Exception as e:self.send({'error':str(e)},400)


if __name__=='__main__':
    port=int(os.environ.get('PORT','8765'))
    print(f'数控审图工作台 http://127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
