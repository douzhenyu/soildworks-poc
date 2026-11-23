"""Turn findings and rule evidence into user-facing review results."""

import re
from graphic_checks import graphic_inventory_read


STATUS = {'error': 'problem', 'warning': 'manual', 'pending': 'missing'}
COLORS = {'problem': (.85,.10,.12), 'pass': (.09,.50,.25),
          'manual': (.76,.51,.02), 'missing': (.76,.51,.02)}


def drawable_results(results):
    for item in results:
        if item.get('box') and item.get('page'):
            yield item


def group_findings(results):
    groups = {}
    for index, row in enumerate(results):
        key = (row.get('page'), row['rule'], row['status'], row.get('method'), row['findingId']) if row.get('findingId') is not None else (index,)
        if key not in groups:
            groups[key] = dict(row, highlightBoxes=[], elementIds=[], citedElementIds=[])
        finding = groups[key]
        for field in ('elementIds', 'citedElementIds'):
            finding[field] = list(dict.fromkeys(finding[field] + row.get(field, [])))
        for box in [row.get('box'), *row.get('highlightBoxes', [])]:
            if not box:
                continue
            if not finding.get('box'):
                finding['box'] = box
            elif list(box) != list(finding['box']) and list(box) not in finding['highlightBoxes']:
                finding['highlightBoxes'].append(list(box))
    return list(groups.values())


def publish_results(results, summaries):
    issues, manual_notes = [], []
    by_rule = {row['rule']: dict(row, passedChecks=list(row.get('passedChecks', []))) for row in summaries}
    for row in group_findings(results):
        if row['status'] == 'pass':
            summary = by_rule.setdefault(row['rule'], dict(rule=row['rule'], status='pass', reason=row['reason']))
            summary.setdefault('passedChecks', []).append(row)
        elif row['status'] == 'problem':
            issues.append(row)
        else:
            manual_notes.append(row)
    for row in issues:
        summary = by_rule.setdefault(row['rule'], dict(rule=row['rule'], reason=row['reason']))
        if summary.get('status') != 'problem':
            summary.update(status='problem', reason=row['reason'])
    return issues, manual_notes, list(by_rule.values())


def _box(bounds, size):
    x0,y0,x1,y1 = bounds
    width,height = size
    return [max(0,x0/width), max(0,1-y1/height), min(1,x1/width), min(1,1-y0/height)]


def _positive(rule, check):
    result = check.get('result','')
    if rule == 8 and result.startswith('present_'):
        values = '、'.join(dict.fromkeys(note['text'] for note in check['values']))
        return check['field']+'已填写', f'单元格显示“{values}”。', check['cellBounds'], None
    if rule == 3 and result in ('matched_visible_feature_group','matched_pattern_group','matched_wizard_group'):
        return '孔组数量一致', f'标注数量 {check["quantity"]} 与已绑定的孔组一致。', None, check['anchor']
    if rule == 3 and result in ('matched_visible_set','matched_base_visible_set'):
        return '腰孔数量一致', f'标注数量 {check["width"]["quantity"]} 与已绑定的腰孔组一致。', None, check['width']['anchor']
    if rule == 5 and result == 'located_from_model_edge':
        return '腰孔单方向定位已确认', '该方向可沿尺寸链追溯至模型边。', None, check['center']
    if rule == 6 and result == 'width_and_length_bound':
        return '腰孔宽度与总长已确认', '两条尺寸绑定到同一腰孔或已核实的孔组。', None, check['width']['anchor']
    if (rule == 6 and result == 'matched_wizard_group' and check.get('depthMatchesModel') is not False
            and check.get('throughMatchesModel') is not False):
        return '孔尺寸与模型对应', '孔径或螺纹规格与孔向导对应。', None, check['anchor']
    return None


def build_review_results(findings, coverage, pages, file_id, packet=None):
    sizes = []
    for page in pages:
        size = re.search(r'SheetSize_m: ([\d.Ee+-]+),([\d.Ee+-]+)', page['raw'])
        sizes.append(tuple(map(float,size.groups())) if size else (.297,.210))
    results = [dict(item,status=item.get('status',STATUS[item['severity']])) for item in findings]
    dimension_locations = {}
    for sheet in (packet or {}).get('pages', []):
        views = {element['view']:element['name'] for element in sheet['elements'] if element['kind']=='view'}
        for element in sheet['elements']:
            if element['kind']=='dimension' and element.get('box'):
                dimension_locations[sheet['page'],views.get(element['view']),element['name']] = element
    for row in coverage:
        rule = row['rule']
        checks = row.get('checks',[])
        existing = [item for item in results if item['rule'] == rule]
        for check in checks:
            page = check.get('page',check.get('width',{}).get('page'))
            if not page or not 1 <= page <= len(sizes):
                continue
            if rule==7 and check['result']=='unverified_span':
                element = dimension_locations.get((page,check['view'],check['objectId']))
                if element:
                    label = ''.join(part['text'] for part in element['renderedText']).strip() or element['name'].split('@')[0]
                    results.append(dict(page=page,rule=rule,title=f'尺寸 {label} 的闭环端点待核对',
                                        reason='已定位到该尺寸标注。请核对两条尺寸界线对应的几何端点，以确认它是否参与闭环。',
                                        box=element['box'],method='规则引擎',status='missing',
                                        objectId=check['objectId'],elementIds=[element['id']]))
                continue
            if rule == 10 and check['result'] == 'passed':
                field = {'company':'公司名称','template_code':'模板编号','process_field':'工艺栏'}[check['field']]
                entry = next(iter(check['entries']),None)
                positive = (field+'符合要求',f'模板显示“{entry["text"]}”。' if entry else '模板文字中未发现工艺栏。',
                            entry['bounds'] if entry else None,None)
            elif (rule == 9 and check['result'] == 'no_visible_note_text'
                  and graphic_inventory_read(pages[page-1]['raw']) and not any(
                      candidate['result']=='unverified_graphic' and candidate['page']==page and
                      (not candidate['fields'] or check['role']+check['field'] in candidate['fields'])
                      for candidate in checks)):
                positive = (check['role']+check['field']+'格未见可见内容',
                            '该格未读到可见文字，也未找到与该格相交的已枚举图形对象。',check['cellBounds'],None)
            else:
                positive = _positive(rule,check)
            if positive:
                title,reason,bounds,anchor = positive
                if anchor:
                    x,y = anchor
                    bounds = (x-.008,y-.004,x+.008,y+.004)
                box = _box(bounds,sizes[page-1]) if bounds else None
                if box and (box[0]>=box[2] or box[1]>=box[3]):
                    box = None
                results.append(dict(page=page,rule=rule,title=title,reason=reason,box=box,
                                    method='规则引擎',status='pass'))
            elif rule == 8 and check.get('result') == 'pending' and check.get('cellBounds'):
                results.append(dict(page=page,rule=rule,title=check['field']+'缺少判断依据',
                                    reason='该单元格缺少可确认的显示值或读取证据。',
                                    box=_box(check['cellBounds'],sizes[page-1]),method='规则引擎',status='missing'))
            elif rule == 3 and check.get('result') in ('unverified_round_claim','wizard_scope_unconfirmed') and check.get('anchor'):
                x,y = check['anchor']
                box = _box((x-.008,y-.004,x+.008,y+.004),sizes[page-1])
                if box[0]>=box[2] or box[1]>=box[3]:
                    box = None
                results.append(dict(page=page,rule=rule,title='孔组数量缺少判断依据',
                                    reason=f'标注“{check["callout"]}”尚未绑定到可确认数量的孔组。',
                                    box=box,method='规则引擎',status='missing'))
            elif rule == 5 and check.get('result')=='relative_spacing_only' and check.get('center'):
                x,y = check['center']
                label = '横向' if check['axis']=='x' else '纵向'
                box = _box((x-.004,y-.004,x+.004,y+.004),sizes[page-1])
                results.append(dict(page=page,rule=rule,title=f'腰孔{label}定位待核对',
                                    reason=f'已确认{label}槽间距，请核对槽组是否有对应的边距或明确的对称基准。',
                                    box=box if box[0]<box[2] and box[1]<box[3] else None,
                                    method='规则引擎',status='missing'))
        if row['status'] == 'checked':
            if not existing and not any(item['rule']==rule for item in results):
                results.append(dict(page=None,rule=rule,title='没问题',reason=row['reason'],box=None,
                                    method='规则引擎',status='pass'))
            continue
        if rule == 12 and existing:
            continue
        missing_checks = {'pending','unverified_span','unresolved','unverified_round_claim',
                          'wizard_scope_unconfirmed','unresolved_scope','relative_spacing_only'}
        missing = row['status']=='pending' or any(check.get('result') in missing_checks for check in checks) or rule==12
        status = 'missing' if missing else 'manual'
        if not any(item['rule']==rule and item['status']==status for item in results):
            results.append(dict(page=None,rule=rule,title='缺少判断依据' if missing else '需要人工审核',
                                reason=row['reason'],box=None,method='规则引擎',status=status))
    for index, item in enumerate(results,1):
        item['id'] = f'{file_id}-{index}'
    return results
