"""Expose one drawing's evidence through a read-only MCP stdio tool."""

import hashlib
import math
import json
import re
import sys
from itertools import combinations
from pathlib import Path
from size_constraint_checks import constraint_findings, overall_dimension_candidates
from hole_checks import _inventories, repeated_hole_counts
from slot_checks import numbers, same_circle, same_line


TOOL = {
    'name': 'read_drawing_evidence',
    'description': '读取指定图纸页的 SolidWorks API 元素和 PDF 坐标。',
    'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    'inputSchema': {'type': 'object', 'properties': {
        'page': {'type': 'integer', 'description': '从 1 起的 PDF 页码'},
        'kind': {'type': 'string', 'description': '元素类型。dimensionIndex 返回精简尺寸索引，straightEdgeIndex 返回直边长度及端点，dimension 返回完整尺寸证据。省略则返回所有类型'},
        'relatedTo': {'type':'string','description':'本页尺寸元素 ID。查找全图引用同一模型、配置和同一持久实体的尺寸，可用 offset 翻页。'},
        'kinds': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 6,
                  'description': '一次读取同一页的多个元素类型，每类返回首批及下一页偏移。'},
        'elementIds': {'type': 'array', 'items': {'type':'string'}, 'minItems':1, 'maxItems':40,
                       'description':'按本页元素 ID 读取完整证据。可用 offset 翻页。'},
        'offset': {'type': 'integer', 'description': '分页偏移量，默认 0'}},
        'required': ['page'], 'additionalProperties': False},
}
DIMENSION_TOOL = {
    'name': 'check_dimension_constraints',
    'description': '按需检查 R03 孔组和圆角数量、R05 孔组定位、R06 外廓跨度或 R07 闭环。',
    'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    'inputSchema': {'type':'object', 'properties':{
        'rule':{'type':'integer','enum':[3,5,6,7]},
        'page':{'type':'integer','description':'目标页码。计算会核对同模型同配置的其他页。'}},
        'required':['rule','page'],'additionalProperties':False},
}
DIFF_TOOL = {
    'name': '2D_vs_3D_Diff',
    'description': '按页核对已绑定的孔口直径和标题栏声明的钣金板厚。problem 是确定性红框。',
    'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    'inputSchema': {'type': 'object', 'properties': {'page': {'type': 'integer'}},
                    'required': ['page'], 'additionalProperties': False},
}
TOOLS = (TOOL, DIMENSION_TOOL, DIFF_TOOL)


def diameter_diff(packet, page):
    if type(page) is not int or not 1 <= page <= len(packet['pages']):
        return {'error': '请输入有效页码。'}
    sheet = packet['pages'][page - 1]
    elements = sheet['elements']
    models = packet.get('models', {})
    views = {e['view']: models.get(e.get('modelKey'), e.get('spatialModel'))
             for e in elements if e['kind'] == 'view'}
    checks, unverified = [], []
    for element in elements:
        if element['kind'] != 'dimension':
            continue
        data = element.get('constraintEvidence') or {}
        if data.get('typeCode') != 6 or element.get('dangling'):
            continue
        text = ''.join(fragment['text'] for fragment in element.get('renderedText', []))
        text = text.replace('<MOD-DIAM>', 'Ø')
        match = re.fullmatch(
            r'\s*[(（]?\s*(?:(?:\d+\s*[×xX]\s*)*\d+\s*-\s*)?'
            r'([⌀ØøΦφϕ]?)\s*(\d+(?:[.,]\d+)?)\s*(?:通单壁|贯穿|通孔|完全贯穿)?'
            r'\s*(?:两侧对称)?\s*(?:[)）])?\s*',
            text)
        has_diameter = match and (match[1] or any('<MOD-DIAM>' in part for part in element.get('textParts', [])))
        model = views.get(element['view'])
        candidates, wizard_sizes = [], []
        attachments = data.get('attachments', [])
        if (not has_diameter or (sheet.get('drawingUnits') or [None])[0] != 0
                or not element.get('box') or element.get('dangling') is not False
                or data.get('visibilityApi') in (2, 3) or len(attachments) != 1):
            unverified.append(element['id'])
            continue
        attachment = attachments[0]
        if model:
            ref = (attachment.get('persistentReferences') or {}).get('model')
            if ref:
                for part in model['components']:
                    if model['kind'] == 'assembly' and (
                        part['id'] != attachment.get('component') or
                        part['configuration'] != attachment.get('componentConfiguration')
                    ):
                        continue
                    for hole in part.get('circularHoles', []):
                        if ref not in hole.get('rimEdgePersistentIds', []):
                            continue
                        candidates.append(hole)
                        for wizard in part.get('wizardHoles', []):
                            if hole.get('wizardFeatureId') == wizard['id']:
                                nominal = re.fullmatch(r'M(\d+(?:\.\d+)?)(?:[xX×]\d+(?:\.\d+)?)?',
                                                       wizard.get('fastenerSize') or '')
                                if nominal:
                                    wizard_sizes.append(float(nominal[1]))
        system_value = data.get('systemValue')
        bound_thread = (attachment.get('selectionType') == 39 and not attachment.get('isNull')
                        and any(record.startswith('DimensionThreadSymbol:')
                                for record in element.get('associationRecords', [])))
        actual_values = {}
        if len(candidates) == 1:
            actual_values['关联孔壁直径'] = candidates[0]['diameterM'] * 1000
        if (len(candidates) == 1 or bound_thread or attachment.get('modelCircle')) and system_value and math.isfinite(system_value) and system_value > 0:
            actual_values['SolidWorks 关联尺寸'] = system_value*1000
        if len(set(wizard_sizes)) == 1:
            actual_values['关联孔向导公称直径'] = wizard_sizes[0]
        if not actual_values:
            unverified.append(element['id'])
            continue
        drawn_mm = float(match[2].replace(',', '.'))
        if not math.isfinite(drawn_mm) or drawn_mm <= 0:
            unverified.append(element['id'])
            continue
        actual_mm = next(iter(actual_values.values()))
        mismatch = not any(math.isclose(drawn_mm, value, rel_tol=0, abs_tol=1e-6)
                           for value in actual_values.values())
        sources = '、'.join(f'{label} Φ{value:g} mm' for label,value in actual_values.items())
        subject = '孔径' if candidates or bound_thread else '直径'
        checks.append(dict(page=page, rule=6, status='problem' if mismatch else 'pass',
                           title=f'2D 与 3D {subject}' + ('不一致' if mismatch else '一致'),
                           reason=f'图面显示 Φ{drawn_mm:g} mm。核验依据为{sources}。',
                           drawingDiameterMm=drawn_mm, modelDiameterMm=actual_mm,
                           elementIds=[element['id']], box=element['box']))
    return dict(page=page, checks=checks, unverifiedElementIds=unverified)


def drawing_model_diff(packet, page):
    result = diameter_diff(packet, page)
    if 'error' in result:
        return result
    sheet = packet['pages'][page - 1]
    if (sheet.get('drawingUnits') or [None])[0] != 0:
        return result
    models = packet.get('models', {})
    for field in sheet['elements']:
        if field['kind'] != 'titleField' or field.get('field') != '材料' or not field.get('box'):
            continue
        source = field.get('sourceView') or {}
        views = [e for e in sheet['elements'] if e['kind'] == 'view' and e.get('name') == source.get('view')]
        if len(views) != 1 or not source.get('model'):
            continue
        view = views[0]
        model = models.get(view.get('modelKey'), view.get('spatialModel'))
        if not model or model.get('kind') != 'part' or len(model.get('components', [])) != 1:
            continue
        if (model['path'].replace('\\', '/').casefold() != source['model'].replace('\\', '/').casefold()
                or model['configuration'].casefold() != (source.get('configuration') or '').casefold()):
            continue
        metadata = model['components'][0].get('sheetMetal', [])
        values = {v['text'].strip() for v in field.get('values', []) if v.get('text', '').strip()}
        if len(metadata) != 1 or len(values) != 1:
            continue
        text = values.pop()
        declarations = re.findall(r'(?<![A-Za-z])[δΔTt]\s*=?\s*(\d+(?:[.,]\d+)?)(?:\s*mm)?(?=$|[\s,;，；)])', text, re.I)
        thickness = metadata[0].get('thicknessM')
        if len(declarations) != 1 or not thickness or not math.isfinite(thickness) or thickness < 0:
            continue
        drawn_mm = float(declarations[0].replace(',', '.'))
        if drawn_mm <= 0 or not math.isfinite(drawn_mm):
            continue
        actual_mm = thickness * 1000
        mismatch = not math.isclose(drawn_mm, actual_mm, rel_tol=0, abs_tol=1e-6)
        result['checks'].append(dict(page=page, rule=6, status='problem' if mismatch else 'pass',
                                    title='标题栏板厚与 3D 钣金板厚' + ('不一致' if mismatch else '一致'),
                                    reason=f'标题栏材料显示 {text}，声明板厚 {drawn_mm:g} mm，关联钣金零件板厚为 {actual_mm:g} mm。',
                                    drawingThicknessMm=drawn_mm, modelThicknessMm=actual_mm,
                                    elementIds=[field['id']], box=field['box']))
    return result


def _short_reference(match):
    return _reference_token(match[0])


def _reference_token(value):
    return 'ref#' + hashlib.sha256(value.encode('ascii')).hexdigest()[:16] if value else None


def _compact_references(value):
    return re.sub(r'[A-Za-z0-9+/]{100,}={0,2}', _short_reference, value)


def model_summaries(packet):
    models = dict(packet.get('models', {}))
    for page in packet['pages']:
        for view in page['elements']:
            if view.get('modelKey') and view.get('spatialModel'):
                models.setdefault(view['modelKey'], view['spatialModel'])

    def select(rows, fields):
        return [{key: row[key] for key in fields if key in row} for row in rows]

    return {
        key: {
            'boundingBoxM': model.get('boundingBoxM'),
            'sheetMetal': [row for part in model['components']
                           for row in select(part.get('sheetMetal', []), ('name', 'thicknessM', 'defaultBendRadiusM', 'kFactor'))],
            'bends': [row for part in model['components']
                      for row in select(part.get('bends', []), ('name', 'angleDeg', 'innerRadiusM'))],
            'holeGroups': [row for part in model['components']
                           for row in select(part.get('holeGroups', []), ('type', 'fastenerSize', 'diameterM',
                                                                        'cylindricalDepthM', 'throughConfirmed',
                                                                        'parametersM', 'count'))],
            'slotGroups': [row for part in model['components']
                           for row in select(part.get('slotGroups', []), ('widthM', 'lengthM', 'depthM', 'count'))],
        }
        for key, model in models.items()
    }


def _batch(elements, offset, budget):
    batch = []
    size = 0
    for element in elements[offset:offset+40]:
        entry = dict(element)
        entry.pop('spatialModel', None)
        # Preserve relationships without using the full serialized reference token.
        if 'apiRecord' in entry:
            entry['apiRecord'] = _compact_references(entry['apiRecord'])
        if 'associationRecords' in entry:
            entry['associationRecords'] = [_compact_references(line) for line in entry['associationRecords']]
        if 'constraintEvidence' in entry:
            constraint = entry['constraintEvidence']
            attachments = []
            for original in constraint['attachments']:
                attachment = dict(original)
                if refs := attachment.get('persistentReferences'):
                    attachment['persistentReferences'] = {scope:_reference_token(ref) for scope,ref in refs.items()}
                attachments.append(attachment)
            entry['constraintEvidence'] = dict(constraint, attachments=attachments,
                                               annotationPersistentId=_reference_token(constraint['annotationPersistentId']))
        if 'annotationPersistentId' in entry:
            entry['annotationPersistentId'] = _reference_token(entry['annotationPersistentId'])
        if 'attachedDrawingReferences' in entry:
            entry['attachedDrawingReferences'] = [_reference_token(ref) for ref in entry['attachedDrawingReferences']]
        size += len(json.dumps(entry, ensure_ascii=False))
        if batch and size > budget:
            break
        batch.append(entry)
    end = offset+len(batch)
    return dict(elements=batch, total=len(elements), nextOffset=end if end < len(elements) else None)


def _model_attachment_keys(element, views):
    model = views.get(element.get('view'))
    if not model:
        return set()
    return {(model, a.get('component'), a['persistentReferences']['model'])
            for a in (element.get('constraintEvidence') or {}).get('attachments', [])
            if (a.get('persistentReferences') or {}).get('model')}


def evidence_slice(packet, args):
    page = args.get('page')
    offset = args.get('offset', 0)
    if type(page) is not int or not 1 <= page <= len(packet['pages']) or type(offset) is not int or offset < 0:
        return {'error': '请输入有效的页码和从零开始的偏移量。'}
    sheet = packet['pages'][page-1]
    meta = {key:value for key,value in sheet.items() if key!='elements'}
    if 'elementIds' in args:
        ids = args['elementIds']
        if (not isinstance(ids,list) or not 1 <= len(ids) <= 40
                or not all(isinstance(ident,str) and ident for ident in ids)
                or any(key in args for key in ('kind','kinds','relatedTo'))):
            return {'error':'请单独提供 1 至 40 个本页元素 ID，可附带 offset。'}
        by_id = {e['id']: e for e in sheet['elements']}
        wanted = list(dict.fromkeys(ids))
        return dict(meta, **_batch([by_id[ident] for ident in wanted if ident in by_id], offset, 24000),
                    missingElementIds=[ident for ident in wanted if ident not in by_id])
    if 'relatedTo' in args:
        seed = next((e for e in sheet['elements'] if e['kind']=='dimension' and e['id']==args['relatedTo']),None)
        if seed is None:
            return {'error':'relatedTo 必须是指定页上的尺寸元素 ID。'}
        views = {e.get('view'):e.get('modelKey') for e in sheet['elements'] if e['kind']=='view'}
        keys = _model_attachment_keys(seed,views)
        matches = []
        if keys:
            for other in packet['pages']:
                views = {e.get('view'):e.get('modelKey') for e in other['elements'] if e['kind']=='view'}
                for e in other['elements']:
                    shared = keys & _model_attachment_keys(e,views)
                    if shared and (other is not sheet or e['id']!=seed['id']):
                        matches.append(dict(e, evidencePage=other['page'],
                                            sharedModelReferences=sorted({_reference_token(k[2]) for k in shared})))
        return dict(meta, **_batch(matches,offset,24000), relatedTo=seed['id'], referenceCount=len(keys),
                    scope='共享持久引用表示至少有一个共同关联实体。请分别核对其余尺寸端点、方向及引线覆盖范围。空结果也可能表示引用未提取。')
    def elements_of_kind(kind):
        if kind == 'straightEdgeIndex':
            return [dict(id=e['id'], view=e['view'], box=e.get('box'),
                         lengthMm=round(math.dist(points[:3], points[3:])*1000, 4),
                         endpointsMm=[round(v*1000, 4) for v in points])
                    for e in sheet['elements'] if e.get('shape')=='line'
                    and len(points := e.get('modelCoordinatesM') or [])==6]
        if kind == 'dimensionIndex':
            return [dict(id=e['id'], view=e['view'], name=e.get('name'), box=e.get('box'),
                         text=''.join(part['text'] for part in e.get('renderedText', [])),
                         parentheses=e.get('parentheses'))
                    for e in sheet['elements'] if e['kind']=='dimension']
        return [e for e in sheet['elements'] if not kind or e['kind']==kind]

    kinds = args.get('kinds')
    if kinds is not None:
        if (not isinstance(kinds, list) or not 1 <= len(kinds) <= 6 or offset or args.get('kind')
                or not all(isinstance(kind, str) and kind for kind in kinds)):
            return {'error': '批量查询请提供 1 至 6 个类型。续查某一类型时改用 kind 和 offset。'}
        groups = {kind:_batch(elements_of_kind(kind), 0, 7000)
                  for kind in dict.fromkeys(kinds)}
        return dict(meta, groups=groups)
    kind = args.get('kind')
    return dict(meta, **_batch(elements_of_kind(kind), offset, 24000))


def visible_hole_groups(packet, page, raw_pages=None):
    groups = {}
    dimensions = {}
    walls = {}
    if raw_pages and len(raw_pages)==len(packet['pages']):
        for sheet, raw in zip(packet['pages'],raw_pages):
            views = [e for e in sheet['elements'] if e['kind']=='view']
            blocks = re.split(r'^View: ',raw['raw'],flags=re.M)[1:]
            if len(views)!=len(blocks):
                continue
            for view,block in zip(views,blocks):
                if view.get('modelKey') and 'ModelBoreWall:' in block:
                    _,_,_,_,bore_walls = _inventories(block)
                    walls.setdefault(view['modelKey'],{}).update((w['wallId'],w) for w in bore_walls)
    for sheet in packet['pages']:
        models = {e['view']:e.get('modelKey') for e in sheet['elements'] if e['kind']=='view'}
        for e in sheet['elements']:
            model = models.get(e.get('view'))
            if not model:
                continue
            if e['kind']=='dimension':
                for attachment in (e.get('constraintEvidence') or {}).get('attachments',[]):
                    for circle in attachment.get('modelCircle',[]):
                        dimensions.setdefault(model,[]).append((circle,sheet['page'],e['id']))
            if sheet['page']!=page:
                continue
            parts = e.get('apiRecord','').split(' | ')
            if e.get('shape')=='circle':
                circle = e.get('modelCoordinatesM')
                owner = ''
            elif e.get('visibleElementIds') and len(parts)>=5 and parts[0].startswith('ModelCircularOpening: '):
                circle = numbers(parts[2],7)
                owner = next((p[6:] for p in parts[3:] if p.startswith('owner=')), '')
                if not owner:
                    continue
            else:
                continue
            if not circle or len(circle)!=7:
                continue
            axis = circle[3:6]
            sign = next((1 if v>0 else -1 for v in axis if abs(v)>1e-6),1)
            direction = tuple(round(sign*v,6) for v in axis)
            distance = sum(x*v for x,v in zip(circle[:3],axis))
            center = tuple(round((x-distance*v)*1e6) for x,v in zip(circle[:3],axis))
            grouping = owner or round(sign*distance*1e6)
            positions = groups.setdefault((model,e['view'],grouping,round(circle[6]*2000,4),direction),{})
            positions.setdefault(center,[]).append((circle,e['id']))
    checks = []
    for (model,view,grouping,diameter,direction),positions in groups.items():
        refs = set()
        covered = 0
        spans = {}
        for center,rims in positions.items():
            matches = {(sheet,ref) for circle,_ in rims for other,sheet,ref in dimensions.get(model,[])
                       if same_circle(circle,other)}
            covered += bool(matches)
            refs.update(matches)
            matching_walls = [w for w in walls.get(model,{}).values()
                              if abs(w['cylinder'][6]*2000-diameter)<.001
                              and any(same_circle(circle,rim) for circle,_ in rims for rim in w['rims'])]
            if len(matching_walls)==1:
                wall = matching_walls[0]
                axis = wall['cylinder'][3:6]
                stations = {round(sum(x*v for x,v in zip(rim[:3],axis)),7) for rim in wall['rims']
                            if abs(rim[6]*2000-diameter)<.001}
                if len(stations)==2:
                    span = round((max(stations)-min(stations))*1000,4)
                    if span>0:
                        boundaries = sorted({tuple(round(v*1000,4) for v in rim[:3])
                                             for rim in wall['rims'] if abs(rim[6]*2000-diameter)<.001})
                        spans.setdefault(span,[]).append(dict(elementId=rims[0][1],
                                                             axisPointMm=[v/1000 for v in center],
                                                             wallBoundaryCentersMm=boundaries,
                                                             wallFeatureType=wall['featureType']))
        checks.append(dict(view=view,diameterMm=diameter,axis=direction,visibleAxes=len(positions),
                           source='modelOpening' if isinstance(grouping,str) else 'coplanarVisibleCircles',
                           directlyReferencedAxes=covered,
                           cylinderSpans=[dict(spanMm=span,axisCount=len(axes),axes=axes[:60])
                                          for span,axes in sorted(spans.items())],
                           exampleElementIds=[rims[0][1] for rims in positions.values()][:8],
                           relatedDimensions=[dict(page=p,id=i) for p,i in sorted(refs)][:20]))
    return sorted(checks,key=lambda row:(row['directlyReferencedAxes']>0,-row['visibleAxes']))


def visible_shoulder_angles(sheet):
    vertices = {}
    inclinations = []
    for edge in sheet['elements']:
        points = edge.get('pdfEndpointsPt')
        model = edge.get('modelCoordinatesM')
        if edge.get('shape')!='line' or not points or not all(points) or not model:
            continue
        dx,dy = (abs(b-a) for a,b in zip(*points))
        if min(dx,dy)>.01 and math.hypot(dx,dy)>=2:
            angle = math.degrees(math.atan2(dy,dx))
            inclinations.append(dict(view=edge['view'],elementIds=[edge['id']],
                                     toHorizontalDeg=round(angle,2),toVerticalDeg=round(90-angle,2),
                                     pdfEndpointsPt=points,
                                     modelEndpointsMm=[[round(value*1000,4) for value in endpoint]
                                                       for endpoint in (model[:3],model[3:])],
                                     modelDeltaMm=[round((b-a)*1000,4) for a,b in zip(model[:3],model[3:])],
                                     modelLengthMm=round(math.dist(model[:3],model[3:])*1000,4)))
        for end, point in enumerate(points):
            vector = tuple(b-a for a,b in zip(point,points[1-end]))
            if math.hypot(*vector)<2:
                continue
            key = (edge['view'],tuple(round(v,7) for v in model[end*3:end*3+3]))
            vertices.setdefault(key,[]).append((edge['id'],vector))
    angles = []
    for (view,_),edges in vertices.items():
        straight = [(i,v) for i,v in edges if min(map(abs,v))<.01]
        sloped = [(i,v) for i,v in edges if min(map(abs,v))>=.01]
        for first,a in straight:
            for second,b in sloped:
                cross = a[0]*b[1]-a[1]*b[0]
                dot = sum(x*y for x,y in zip(a,b))
                angle = round(math.degrees(math.atan2(abs(cross),dot)),2)
                if 1<angle<179:
                    angles.append(dict(view=view,elementIds=[first,second],includedAngleDeg=angle,
                                       supplementaryAngleDeg=round(180-angle,2)))
    return angles[:60], inclinations[:60]


def rounded_corner_angles(sheet):
    lines_by_view = {}
    for edge in sheet['elements']:
        points = edge.get('modelCoordinatesM')
        if edge.get('shape') != 'line' or not points or len(points) != 6:
            continue
        first, second = points[:3], points[3:]
        direction = [b-a for a,b in zip(first,second)]
        length = math.hypot(*direction)
        if length > .0001:
            lines_by_view.setdefault(edge['view'], []).append(
                (edge['id'], first, second, [value/length for value in direction]))
    candidates = []
    for arc in sheet['elements']:
        circle = arc.get('modelCoordinatesM')
        if arc.get('shape') != 'arc' or not circle or len(circle) != 7:
            continue
        center, normal, radius = circle[:3], circle[3:6], circle[6]
        tangents = []
        for ident, first, second, direction in lines_by_view.get(arc['view'], []):
            for endpoint, sign in ((first, 1), (second, -1)):
                radial = [x-y for x,y in zip(endpoint, center)]
                if (abs(math.hypot(*radial)-radius) <= 1e-6
                    and abs(math.fsum(a*b for a,b in zip(radial,normal))) <= 1e-6
                    and abs(math.fsum(a*b for a,b in zip(radial,direction))) <= 1e-6
                    and abs(math.fsum(a*b for a,b in zip(normal,direction))) <= .001):
                    tangents.append((ident, [sign*v for v in direction], endpoint))
        if len(tangents) != 2 or tangents[0][0] == tangents[1][0]:
            continue
        first, second = tangents
        cosine = math.fsum(a*b for a,b in zip(first[1],second[1]))
        angle = math.degrees(math.acos(max(-1, min(1, cosine))))
        if 1 < angle < 179:
            a, b = first[1], second[1]
            separation = [y-x for x,y in zip(first[2],second[2])]
            along_a = math.fsum(x*y for x,y in zip(separation,a))
            along_b = math.fsum(x*y for x,y in zip(separation,b))
            distance = (along_a-cosine*along_b)/(1-cosine*cosine)
            intersection = [x+distance*v for x,v in zip(first[2],a)]
            candidates.append(dict(view=arc['view'], elementIds=[arc['id'], first[0], second[0]],
                                   theoreticalCornerMm=[round(v*1000,4) for v in intersection],
                                   radiusMm=round(radius*1000,4), includedAngleDeg=round(angle,2),
                                   turnAngleDeg=round(180-angle,2)))
            if len(candidates) == 60:
                break
    return candidates


def coplanar_circles(first, second):
    normal = first[3:6]
    other = second[3:6]
    lengths = math.hypot(*normal)*math.hypot(*other)
    if not lengths:
        return False
    cosine = math.fsum(a*b for a,b in zip(normal,other))/lengths
    offset = math.fsum((a-b)*n for a,b,n in zip(first[:3],second[:3],normal))
    return abs(cosine) >= .99999 and abs(offset) <= 1e-6*math.hypot(*normal)


def radius_evidence(sheet):
    result = {}
    arcs = {}
    radius_dimensions = []
    for e in sheet['elements']:
        if e.get('shape')=='arc' and e.get('modelCoordinatesM') and e.get('box'):
            radius = round(e['modelCoordinatesM'][6]*1000,4)
            arcs.setdefault((e['view'],radius),[]).append(e)
        if e['kind']=='dimension':
            label = ''.join(t['text'] for t in e.get('renderedText',[]))
            match = re.match(r'^\s*\(?\s*(?:(\d+)\s*[-‐‑–—−×xX*]\s*)?R\s*(\d+(?:\.\d+)?)',
                             label, re.IGNORECASE)
            if match:
                attachments = (e.get('constraintEvidence') or {}).get('attachments',[])
                circles = [c for a in attachments for c in a.get('modelCircle',[])]
                radius_dimensions.append((e,label,match,circles))
    result['arcCandidates'] = [
        dict(view=view,radiusMm=radius,visibleArcCount=len(edges),elementIds=[e['id'] for e in edges[:30]],
             arcLocations=[dict(elementId=e['id'],box=e['box'],
                                centerMm=[round(v*1000,4) for v in e['modelCoordinatesM'][:3]])
                           for e in edges[:30]])
        for (view,radius),edges in list(arcs.items())[:60]]
    callouts = []
    for dim, label, match, circles in radius_dimensions[:60]:
        radius = float(match[2])
        matching_view_edges = arcs.get((dim['view'], round(radius,4)), [])
        matched = [edge['id'] for (view,_),edges in arcs.items() if view==dim['view']
                   for edge in edges if any(same_circle(c,edge['modelCoordinatesM']) for c in circles)]
        coplanar = [edge['id'] for edge in matching_view_edges
                    if any(coplanar_circles(c,edge['modelCoordinatesM']) for c in circles)]
        callouts.append(dict(view=dim['view'], dimensionId=dim['id'],
                             text=label, box=dim.get('box'), parentheses=dim.get('parentheses'),
                             declaredCount=int(match[1]) if match[1] else None,
                             declaredRadiusMm=radius,
                             attachedRadiiMm=sorted({round(c[6]*1000,4) for c in circles}),
                             matchedArcIds=matched[:30], coplanarArcIds=coplanar[:30],
                             coplanarArcCount=len(coplanar)))
    result['radiusCallouts'] = callouts
    return result


def hole_feature_evidence(sheet):
    related = {}
    candidates = []
    for element in sheet['elements']:
        record = element.get('apiRecord', '')
        kind, _, content = record.partition(': ')
        if kind == 'ModelWizardHole':
            candidates.append((element, content.split(' | ', 1)[0]))
        elif kind == 'ModelWizardHolePoint':
            owner = content.split(' | ', 1)[0]
            related.setdefault(owner, []).append(element)
        elif kind in ('ModelCircularOpening', 'ModelBoreWall') and ' | owner=' in content:
            owner = content.split(' | owner=', 1)[1].split(' | ', 1)[0]
            related.setdefault(owner, []).append(element)
    return [
        dict(view=element['view'], elementId=element['id'],
             specification=_compact_references(element['apiRecord']),
             geometry=[dict(view=edge['view'], elementId=edge['id'], box=edge.get('box'),
                            pdfAnchorPt=edge.get('pdfAnchorPt'), boundsBasis=edge.get('boundsBasis'),
                            visibleElementIds=edge.get('visibleElementIds', []))
                       for edge in related.get(owner, []) if edge.get('box') or edge.get('pdfAnchorPt')][:20])
        for element, owner in candidates[:40]
    ]


def unreferenced_section_edges(sheet, *, ordinary=False):
    view_types = (4, 6, 7, 8) if ordinary else (2, 3)
    views = {e['view']: e for e in sheet['elements'] if e['kind'] == 'view'
             and (e.get('metadata') or {}).get('type') in view_types}
    sections = {view: [] for view in views}
    attachments = {view: [] for view in views}
    dimension_spans = {view: [] for view in views}
    dimension_counts = dict.fromkeys(views, 0)
    for element in sheet['elements']:
        view = element.get('view')
        if view not in views:
            continue
        if element['kind'] == 'dimension':
            dimension_counts[view] += 1
        if element['kind'] == 'dimension' and not element.get('dangling'):
            constraint = element.get('constraintEvidence') or {}
            for attachment in constraint.get('attachments', []):
                attachments[view].extend(attachment.get('modelLineEndpointsM', []))
            span = constraint.get('verifiedLinearSpan') or {}
            points = span.get('pdfPointsPt') or []
            if span.get('axis') in ('x', 'y') and len(points) == 2 and all(points):
                axis = 'xy'.index(span['axis'])
                dimension_spans[view].append((axis, sorted(point[axis] for point in points)))
        elif (element.get('shape') == 'line' and len(element.get('modelCoordinatesM') or []) == 6
              and len(points := element.get('pdfEndpointsPt') or []) == 2 and all(points)
              and math.dist(*points) > .1):
            sections[view].append(element)

    def shares_projection(a, b):
        return min(max(math.dist(a[0], b[0]), math.dist(a[1], b[1])),
                   max(math.dist(a[0], b[1]), math.dist(a[1], b[0]))) <= .1

    def local_spans(edges, unbound_ids, view):
        transform = view.get('transform')
        if not transform or len(transform) < 13 or not view.get('pdfBoxPt'):
            return []
        outline = view['pdfBoxPt']
        extents = [outline[2]-outline[0], outline[3]-outline[1]]
        spans = {}
        for a, b in combinations(edges, 2):
            unbound = [edge['id'] in unbound_ids for edge in (a,b)]
            if not any(unbound):
                continue
            points = [edge['pdfEndpointsPt'] for edge in (a, b)]
            lengths = [math.dist(line[:3],line[3:])*1000 for edge in (a,b) if (line := edge['modelCoordinatesM'])]
            for axis in (0, 1):
                along = 1-axis
                if any(abs(p[0][axis]-p[1][axis]) > .1 for p in points):
                    continue
                first, second = (sorted(p, key=lambda q:q[along]) for p in points)
                stations = sorted((first[0][axis],second[0][axis]))
                if stations[1]-stations[0] <= .1:
                    continue
                aligned = all(abs(p[along]-q[along]) <= .1 for p,q in zip(first,second))
                source = 'parallel-edge-span'
                bridge = None
                witnesses = [first[0],second[0]]
                if aligned:
                    if not all(unbound):
                        continue
                else:
                    gap = max(first[0][along]-second[1][along], second[0][along]-first[1][along])
                    short_end = (0 < gap <= .05*extents[along]
                                 and any(free and 0 < lengths[i] <= 5.0001 and lengths[1-i] >= 2*lengths[i]
                                         for i,free in enumerate(unbound)))
                    if stations[1]-stations[0] > .35*extents[axis]:
                        continue
                    if short_end:
                        source = 'short-end-to-parallel-wall'
                        witnesses = [first[1],second[0]] if first[0][along] < second[0][along] else [first[0],second[1]]
                    elif not ordinary and dimension_counts[view['view']] == 0 and gap < 0:
                        shared = (max(first[0][along],second[0][along]), min(first[1][along],second[1][along]))
                        bridge = next((edge for edge in edges if
                                       (ends := edge['pdfEndpointsPt'])
                                       and abs(ends[0][along]-ends[1][along]) <= .1
                                       and shared[0]-.1 <= ends[0][along] <= shared[1]+.1
                                       and all(abs(p-q) <= .05*extents[axis]
                                               for p,q in zip(sorted(pt[axis] for pt in ends), stations))), None)
                        if not bridge:
                            continue
                        source = 'section-profile-projection'
                        witnesses = [min(pair, key=lambda point: abs(point[along]-bridge['pdfEndpointsPt'][0][along]))
                                     for pair in (first, second)]
                    else:
                        continue
                if any(axis == dim_axis and all(abs(p-q) <= .1 for p,q in zip(stations,dim_stations))
                       for dim_axis,dim_stations in dimension_spans[view['view']]):
                    continue
                delta = [b['modelCoordinatesM'][i]-a['modelCoordinatesM'][i] for i in range(3)]
                if source != 'section-profile-projection' and abs(sum(delta[i]*transform[3*i+2] for i in range(3))) > 1e-6:
                    continue
                span_mm = round(abs(sum(delta[i]*transform[3*i+axis] for i in range(3)))*1000, 4)
                if ordinary and (span_mm > 30 or (source == 'parallel-edge-span' and span_mm > 10)):
                    continue
                key = (axis, *(round(p, 1) for p in stations))
                candidate = dict(axis='x' if axis==0 else 'y', lengthMm=span_mm,
                                 elementIds=[a['id'], b['id']], pdfEndpointsPt=witnesses, source=source)
                if bridge:
                    candidate['profileEdgeId'] = bridge['id']
                bottom = max(point[1] for pair in points for point in pair)
                priority = (source != 'parallel-edge-span', bottom, span_mm)
                if span_mm > 0 and (key not in spans or priority > spans[key][0]):
                    spans[key] = (priority, candidate)
        ranked = sorted(spans.values(), key=lambda row:row[0], reverse=True)
        return [candidate for _,candidate in ranked[:6 if ordinary else 16]]

    result = []
    for view, edges in sections.items():
        candidates = []
        distinct_edges = []
        unbound_ids = set()
        for index, edge in enumerate(edges):
            line = edge['modelCoordinatesM']
            points = edge['pdfEndpointsPt']
            coincident = [i for i, other in enumerate(edges) if i != index
                          and shares_projection(points, other['pdfEndpointsPt'])]
            if any(i < index or not same_line(line, edges[i]['modelCoordinatesM']) for i in coincident):
                continue
            distinct_edges.append(edge)
            if any(same_line(line, attached) for attached in attachments[view]):
                continue
            unbound_ids.add(edge['id'])
            candidates.append(dict(elementId=edge['id'], box=edge.get('box'), pdfEndpointsPt=points,
                                   lengthMm=round(math.dist(line[:3], line[3:])*1000, 4)))
        candidates.sort(key=lambda edge: -edge['lengthMm'])
        spans = local_spans(distinct_edges, unbound_ids, views[view])
        if not ordinary or spans:
            result.append(dict(view=view, viewName=views[view]['name'], viewElementId=views[view]['id'],
                               dimensionCount=dimension_counts[view], candidateCount=len(candidates),
                               edges=[] if ordinary else candidates[:16], localSpanCandidates=spans))
    return result


def call_drawing_tool(packet, name, args, raw_pages=None):
    if not isinstance(args, dict):
        return {'error':'工具参数应为对象。'}
    if name == TOOL['name']:
        return evidence_slice(packet,args)
    if name == DIFF_TOOL['name']:
        return drawing_model_diff(packet, args.get('page'))
    if name != DIMENSION_TOOL['name']:
        return {'error':'未知图纸工具。'}
    rule, page = args.get('rule'), args.get('page')
    if type(rule) is not int or rule not in (3,5,6,7) or type(page) is not int or not 1<=page<=len(packet['pages']):
        return {'error':'请指定 R03、R05、R06 或 R07 及有效页码。'}
    claims = []
    raw_available = bool(raw_pages and len(raw_pages)==len(packet['pages']))
    if rule==5 and not raw_available:
        return {'error':'缺少与 PDF 页对应的孔组关联数据。'}
    if rule in (3,5) and raw_available:
        claims = [(page,claim) for claim in repeated_hole_counts(raw_pages[page-1]['raw'])]
    if rule==3:
        return {'page':page,'rule':rule,'checks':[
            {key:claim[key] for key in ('view','objectId','callout','quantity','result','diameterMm',
             'visibleHoleCount','patternHoleCount','modelUpperBound') if key in claim}
            for _,claim in claims],
            'holeDataAvailable':raw_available, **radius_evidence(packet['pages'][page-1]),
            'scope':'exceeds_bound 表示已绑定圆孔的标注数超过完整零件配置的同径内圆柱面上限。matched_pattern_group 与 matched_visible_feature_group 已核实对应孔组数量。此查询不验证孔深。'}
    checks = []
    for row in constraint_findings(packet,{rule},claims,include_same_view=True):
        if row['page']==page:
            checks.append({key:row.get(key) for key in ('title','reason','view','box','objectId','evidence')})
    result = {'page':page,'rule':rule,'checks':checks,
            'scope':'仅覆盖可核实的模型和尺寸关联。没有候选项时，仍需查阅原始证据和图面。'}
    if rule == 5:
        sheet = packet['pages'][page-1]
        inclinations = {angle['elementIds'][0] for angle in visible_shoulder_angles(sheet)[1]}
        result['roundedShoulders'] = [corner for corner in rounded_corner_angles(sheet)
                                      if inclinations.intersection(corner['elementIds'][1:])]
        circles = [dict(view=e['view'], elementId=e['id'], box=e.get('box'),
                        centerMm=[round(v*1000,4) for v in e['modelCoordinatesM'][:3]],
                        diameterMm=round(e['modelCoordinatesM'][6]*2000,4))
                   for e in packet['pages'][page-1]['elements']
                   if e.get('shape')=='circle' and len(e.get('modelCoordinatesM') or [])==7]
        result.update(circleCenters=circles[:80], circleCenterCount=len(circles))
    if rule==6:
        sheet = packet['pages'][page-1]
        result['unreferencedSectionEdges'] = unreferenced_section_edges(sheet)
        result['localProjectionSpans'] = unreferenced_section_edges(sheet, ordinary=True)
        result['overallDimensionCandidates'] = [candidate for candidate in overall_dimension_candidates(packet)
                                                if candidate['page'] == page]
        result['sectionEdgeScope'] = '每个剖视图和详图独立返回直边及横纵局部跨度候选。short-end-to-parallel-wall 表示短自由端与邻近基准壁的投影间距，基准壁可以已被其他尺寸引用。已验证的同轴同端点跨度已排除。须核对目标边、其他视图和等效尺寸链后再判漏标。'
        result['toleranceNotes'] = [
            dict(elementId=e['id'], view=e['view'], text=e['text'], box=e.get('box'))
            for e in sheet['elements'] if e['kind']=='note'
            and re.search(r'公差|GD\s*&\s*T|\b1804\b|\b2768\b', e.get('text',''), re.IGNORECASE)
        ][:20]
        result['shoulderAngles'], result['edgeInclinations'] = visible_shoulder_angles(sheet)
        result.update(radius_evidence(sheet))
        result['roundedCornerAngles'] = rounded_corner_angles(sheet)
        result['roundedCornerScope'] = '候选来自模型中与支承圆共面且相切的两条直边端部。用图面确认它们与实际圆弧相接，区分折弯与普通圆角，再核对标注。'
        result['holeFeatureCandidates'] = hole_feature_evidence(sheet)
        result['holeFeatureScope'] = '孔向导记录提供模型规格候选。核对所属实体、位置和图面说明后，再区分螺纹孔、压铆件和普通圆孔。'
        result['arcScope'] = '圆弧为候选，圆弧框是支承圆的范围。逐弧确认属于折弯、外轮廓圆角、槽端或孔口，并核对局部标注、通用圆角要求与跨视图覆盖。'
        result['shoulderAngleScope'] = '夹角来自同视图共享模型端点的边。倾角仅相对于图纸横纵轴。确认真实加工角度及设计基准后，再核对直标、等效约束、倒角和其他视图。'
        groups = visible_hole_groups(packet,page,raw_pages)
        result['visibleHoleGroups'] = groups[:40]
        result['visibleHoleGroupCount'] = len(groups)
        result['holeGroupScope'] = '分组依据见 source。共面圆边可能来自多个孔组。cylinderSpans 是匹配到唯一模型孔壁且有两个完整圆边界时测得的圆柱段轴向长度。须核实入口、终止面和孔型才能用作加工深度。同一孔可能出现在不同视图，请按孔轴去重。'
    return result


def serve(path, receipt, raw_path=None):
    packet = json.loads(path.read_text(encoding='utf-8'))
    raw_pages = json.loads(raw_path.read_text(encoding='utf-8')) if raw_path else None
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        method, params = request.get('method'), request.get('params', {})
        reply = {'jsonrpc':'2.0', 'id':request['id']}
        if method == 'initialize':
            reply['result'] = {'protocolVersion':params['protocolVersion'], 'capabilities':{'tools':{}},
                               'serverInfo':{'name':'drawing-evidence', 'version':'1.0.0'}}
        elif method == 'ping':
            reply['result'] = {}
        elif method == 'tools/list':
            reply['result'] = {'tools':list(TOOLS)}
        elif method == 'tools/call':
            args = params.get('arguments') or {}
            data = call_drawing_tool(packet, params.get('name'), args, raw_pages)
            if 'error' not in data:
                with receipt.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(dict(args,tool=params['name']), ensure_ascii=True)+'\n')
            reply['result'] = {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}],
                               'isError':'error' in data}
        else:
            reply['error'] = {'code':-32601, 'message':'Method not found'}
        print(json.dumps(reply, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    serve(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv)>3 else None)
