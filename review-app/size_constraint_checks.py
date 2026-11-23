"""Conservative dimensional checks across views of a referenced part."""

from collections import defaultdict, deque
from math import dist
import re

from dimension_checks import project_model_point
from slot_location_checks import _on_perpendicular, _sketch_to_model


EPS = 1e-6  # Model-space comparison, in metres.
LINEAR_TYPES = {1, 2, 4, 7, 8, 9, 10, 11, 12}


def _dot(a, b):
    return sum(x*y for x,y in zip(a,b))


def _direction(vector):
    sign = next((1 if x>0 else -1 for x in vector if abs(x)>EPS), 1)
    return tuple(round(sign*x,6) for x in vector)


def _view_axis(view, axis):
    return _direction(view['transform'][axis:9:3])


def _project(point, direction):
    return _dot(point, direction)


def _covers(values, endpoints):
    return any(abs(a-b)<=EPS for a in values for b in endpoints)


def _path(edges, start, target):
    queue = deque([(start, [])])
    seen = {start}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        for other, edge in edges[node]:
            if other not in seen:
                seen.add(other)
                queue.append((other, path+[edge]))
    return []


def _full_part(view, envelope):
    context = view.get('dimensionContext', {})
    return (envelope and envelope.get('solidBodies')==1 and envelope.get('status')=='complete'
            and view['metadata']['type'] in (4,5,6,7,8)
            and context.get('cropped') is False and context.get('modelBreak') is False
            and context.get('breakCount')==context.get('breakOuts')==0
            and view.get('transform') and view['transform'][12]>0)


def _dimension_entries(packet):
    views = {}
    grouped = defaultdict(list)
    for page in packet['pages']:
        number = page['page']
        by_view = defaultdict(list)
        for entry in page['elements']:
            by_view[entry['view']].append(entry)
        for key, entries in by_view.items():
            view = next((e for e in entries if e['kind']=='view'), None)
            envelope = next((e for e in entries if e['kind']=='modelEnvelope'), None)
            if not view or not view.get('modelKey') or not view.get('transform'):
                continue
            dims = [e for e in entries if e['kind']=='dimension' and e.get('constraintEvidence')]
            data = dict(page=number, view=view, envelope=envelope, entries=entries, dims=dims,
                        millimetres=bool(page.get('drawingUnits') and page['drawingUnits'][0]==0),
                        dimensionsRead=not any('DIM_ERROR' in error or 'DisplayGeometryReadError' in error
                                               for error in page['extractionErrors']))
            views[number,view['name']] = data
            grouped[view['modelKey']].append(data)
    return views, grouped


def _usable(dim):
    c = dim['constraintEvidence']
    return c['visibilityApi']==1 and not c['shownAsReference'] and dim.get('dangling') is False


def _printed_value_matches(data, dim):
    value = dim['constraintEvidence'].get('systemValue')
    printed = ''.join(part['text'] for part in dim.get('renderedText', [])).strip()
    if not data['millimetres'] or value is None or not re.fullmatch(r'\d+(?:\.\d+)?',printed):
        return False
    precision = len(printed.partition('.')[2])
    return abs(value*1000-float(printed)) <= .5*10**-precision+EPS


def _spans(views, direction):
    for data in views:
        for dim in data['dims']:
            span = dim['constraintEvidence']['verifiedLinearSpan']
            if span and _usable(dim) and _printed_value_matches(data,dim) and _direction(span['modelDirection'])==direction:
                yield data, dim, span['modelPointsM']


def _unverified_parallel(views, direction):
    for data in views:
        view = data['view']
        for dim in data['dims']:
            c = dim['constraintEvidence']
            if c['typeCode'] not in LINEAR_TYPES or c['shownAsReference'] or c['visibilityApi'] in (2,3):
                continue
            span = c['verifiedLinearSpan']
            if span:
                if _direction(span['modelDirection'])!=direction or _usable(dim) and _printed_value_matches(data,dim):
                    continue
            if any(_view_axis(view,i)==direction for i in range(2)):
                return True
    return False


def _result(rule, status, title, reason, data, element, **evidence):
    return dict(rule=rule, status=status, title=title, reason=reason, page=data['page'],
                view=data['view']['name'], box=element.get('box'), objectId=element['id'], evidence=evidence)


def _envelope_checks(views):
    candidates = defaultdict(list)
    for data in views:
        view, envelope = data['view'], data['envelope']
        if not data['millimetres'] or not _full_part(view,envelope) or view['metadata']['type']==5 or not envelope['box']:
            continue
        lines = [e['modelCoordinatesM'] for e in data['entries'] if e.get('shape')=='line']
        if not lines:
            continue
        for axis, name in enumerate(('x','y')):
            direction = _view_axis(view,axis)
            if sum(abs(x)>EPS for x in direction)!=1:
                continue
            limits = sorted(_project(e['modelPointM'],direction) for e in envelope['extrema'] if e['axis']==name)
            if len(limits)!=2 or limits[1]-limits[0]<=EPS:
                continue
            witnesses = [_project(point,direction) for line in lines for point in (line[:3],line[3:])]
            if all(_covers(witnesses,[end]) for end in limits):
                candidates[direction].append((data,axis,limits))
    max_extent = max((limits[1]-limits[0] for rows in candidates.values() for _,_,limits in rows), default=0)
    angular = any(d['constraintEvidence']['typeCode'] in (3,16) and _usable(d) for v in views for d in v['dims'])
    for direction, candidates_on_axis in candidates.items():
        data, axis, limits = max(candidates_on_axis, key=lambda row: row[0]['view']['transform'][12])
        measured = list(_spans(views,direction))
        graph = defaultdict(list)
        positions = []
        def node(point):
            value = _project(point,direction)
            for i, end in enumerate(limits):
                if abs(value-end)<=EPS:
                    return i-2
            return _point_node(positions,point)
        for _,dim,points in measured:
            a,b = (node(p) for p in points)
            graph[a].append((b,dim))
            graph[b].append((a,dim))
        left,right = -2,-1
        chain = _path(graph,left,right)
        label = '横向' if axis==0 else '纵向'
        if chain:
            yield _result(6,'pass',f'{label}总体尺寸已确认',f'{label}两端外廓可由直接尺寸或尺寸链确定。',
                          data,data['envelope'],axis=label,dimensions=[d['id'] for d in chain])
        elif graph[left] or graph[right]:
            uncertain = (_unverified_parallel(views,direction) or not all(v['dimensionsRead'] for v in views)
                         or angular and limits[1]-limits[0]<max_extent-EPS)
            yield _result(6,'missing' if uncertain else 'problem',
                          f'{label}总体尺寸缺少判断依据' if uncertain else f'缺少{label}总体尺寸',
                          f'{label}外廓的两端未找到直接标注或贯通的尺寸链。'
                          + ('请核对未绑定标注或斜向尺寸能否确定外形。' if uncertain else '请核对设计基准，并在能表达两端的视图补标。'),
                          data,data['envelope'],axis=label,modelSpanMm=round((limits[1]-limits[0])*1000,3))
        elif measured:
            yield _result(6,'missing',f'{label}外廓尺寸待核对',
                          f'已识别{label}外廓两端，但已核实的同向尺寸均未连接到两端。请核对其他标注能否唯一确定该跨度。',
                          data,data['envelope'],axis=label,modelSpanMm=round((limits[1]-limits[0])*1000,3))


def _hole_spacing(dim, centers, view, axis):
    transform = view['transform']
    sheet_centers = [project_model_point(p,transform) for p in centers]
    targets = []
    for a in dim['constraintEvidence']['attachments']:
        matched = set()
        for circle in a.get('modelCircle', []):
            matched.update(i for i,p in enumerate(centers) if dist(p,circle[:3])<=EPS)
        for line in a.get('sketchLineApi', []):
            for matrix in a.get('modelToSketch', []):
                if matrix[12] <= 0:
                    continue
                endpoints = [project_model_point(_sketch_to_model(p,matrix),transform) for p in (line[:3],line[3:])]
                matched.update(i for i,p in enumerate(sheet_centers) if _on_perpendicular(p,endpoints,axis))
        if len(matched)==1:
            targets.extend(matched)
    return len(set(targets))==2


def _hole_checks(views, grouped, claims):
    for page, claim in claims:
        pattern = claim['result']=='matched_pattern_group' and claim['quantity']==2
        holes = claim.get('patternHoles' if pattern else 'visibleHoles', [])
        data = views.get((page,claim['view']))
        if (not data or not (pattern or claim['result']=='matched_visible_feature_group')
                or len(holes)!=claim['quantity'] or len(holes)<2):
            continue
        centers = [h['modelCenter'] for h in holes]
        view = data['view']
        family = grouped[view['modelKey']]
        if not pattern and not any(_full_part(v['view'],v['envelope']) for v in family):
            continue
        circles = []
        for center in centers:
            circle = next((e for e in data['entries'] if e.get('shape')=='circle' and e.get('box')
                           and dist(e['modelCoordinatesM'][:3],center)<=EPS
                           and abs(e['modelCoordinatesM'][6]-claim['diameterMm']/2000)<=EPS), None)
            if circle:
                circles.append(circle)
        if len(circles)!=len(centers):
            continue
        for axis in range(2):
            direction = _view_axis(view,axis)
            coordinates = [_project(p,direction) for p in centers]
            cross = [_project(p,_view_axis(view,1-axis)) for p in centers]
            if max(cross)-min(cross)>EPS or max(coordinates)-min(coordinates)<=EPS:
                continue
            spans = list(_spans(family,direction))
            internal = []
            possible_external = False
            anchored = set()
            for source,dim,points in spans:
                values = [_project(p,direction) for p in points]
                matched = [_covers(coordinates,[p]) for p in values]
                if all(matched) and source is data and _hole_spacing(dim,centers,view,axis):
                    internal.append(dim)
                elif any(matched):
                    possible_external = True
                    for point in points:
                        anchored.update(i for i,center in enumerate(centers) if dist(point,center)<=EPS)
            if len(anchored)==len(centers) or not internal and len(holes)!=2:
                continue
            # A centerline at the row midpoint may establish a symmetry datum.
            # Its existence alone cannot establish that relationship.
            sheet_centers = [project_model_point(p,view['transform']) for p in centers]
            middle = (min(p[axis] for p in sheet_centers)+max(p[axis] for p in sheet_centers))/2
            symmetry = any(e['kind']=='centerline' and e.get('visibilityApi')==1 and (b:=e['sheetBoundsM'])
                           and abs(b[axis]-middle)<=EPS and abs(b[axis+2]-middle)<=EPS
                           for e in data['entries'])
            uncertain = (symmetry or _unverified_parallel(family,direction) or not all(v['dimensionsRead'] for v in family)
                         or possible_external)
            label = '横向' if axis==0 else '纵向'
            region = [min(e['box'][0] for e in circles),min(e['box'][1] for e in circles),
                      max(e['box'][2] for e in circles),max(e['box'][3] for e in circles)]
            name = f'{claim["quantity"]}×⌀{claim["diameterMm"]:g} 孔组'
            if not internal:
                yield _result(5,'missing' if uncertain else 'problem',
                              f'{name}{label}中心距待核对' if uncertain else f'{name}缺少{label}中心距',
                              f'两个孔的中心已由模型确认。图纸中未找到可确认的{label}中心距或分别定位两孔的尺寸链。'
                              + ('请核对其他视图中未绑定的尺寸或对称基准。' if uncertain else '请补标中心距或等效定位尺寸。'),
                              data,dict(circles[0],box=region),axis=label,holeCentersM=centers)
                continue
            yield _result(5,'missing' if uncertain else 'problem',
                          f'{name}{label}定位待核对' if uncertain else f'{name}缺少{label}定位',
                          f'已核实{label}孔间距，但未找到孔组至基准的{label}连接。'
                          + ('请核对对称基准或未绑定标注。' if uncertain else '可从设计基准向其中一个孔中心补标定位尺寸。'),
                          data,dict(circles[0],box=region),axis=label,
                          holeCentersM=centers,dimensions=[d['id'] for d in internal])


def _point_node(points, point):
    index = next((i for i,p in enumerate(points) if dist(point,p)<=EPS),len(points))
    if index==len(points):
        points.append(point)
    return index


def _cross_view_loops(views, include_same_view=False):
    if not any(_full_part(data['view'],data['envelope']) for data in views):
        return
    directions = {_direction(s['modelDirection']) for data in views for d in data['dims']
                  if (s:=d['constraintEvidence']['verifiedLinearSpan'])}
    for direction in directions:
        points = []
        edges = []
        for data,dim,ends in _spans(views,direction):
            a,b = (_point_node(points,end) for end in ends)
            edges.append((data,dim,a,b))
        for data,dim,a,b in edges:
            lo,hi = sorted(_project(points[i],direction) for i in (a,b))
            if _project(points[a],direction)>_project(points[b],direction):
                a,b = b,a
            inner = defaultdict(list)
            for source,record,i,j in edges:
                x,y = sorted(_project(points[k],direction) for k in (i,j))
                if x>=lo-EPS and y<=hi+EPS and y-x<hi-lo-EPS:
                    if _project(points[i],direction)>_project(points[j],direction):
                        i,j = j,i
                    inner[i].append((j,(source,record,i,j)))
            path = _path(inner,a,b)
            crosses_views = any(part[0] is not data for part in path)
            if len(path)>=2 and (include_same_view or crosses_views):
                title = '跨视图总尺寸与分段尺寸闭环' if crosses_views else '同视图总尺寸与分段尺寸闭环'
                yield _result(7,'problem',title,
                              '同一模型配置中，总尺寸两端已由分段尺寸逐段相连。可将一条重复约束改为括号参考尺寸。',
                              data,dim,direction=direction,dimensions=[dict(page=p[0]['page'],id=p[1]['id']) for p in path])


def constraint_findings(packet, rules, hole_claims, *, include_same_view=False):
    views, grouped = _dimension_entries(packet)
    if 5 in rules:
        yield from _hole_checks(views,grouped,hole_claims)
    for family in grouped.values():
        if 6 in rules:
            yield from _envelope_checks(family)
        if 7 in rules:
            yield from _cross_view_loops(family,include_same_view)
    if 6 in rules and len(grouped)==1:
        family = next(iter(grouped.values()))
        if any(e.get('apiRecord','').startswith('ModelFeature: SheetMetal |') and e['apiRecord'].endswith('suppressed=False')
               for view in family for e in view['entries']):
            thickness_shown = False
            for page in packet['pages']:
                for note in page['elements']:
                    if note['kind']!='note' or not note.get('confirmedVisible') or not note.get('box'):
                        continue
                    marker = r'(?:板厚\s*[:：=]?\s*)'
                    if note.get('owner')=='2':
                        marker = r'(?:板厚\s*[:：=]?\s*|[δΔ]\s*)'
                    match = re.search(marker+r'(\d+(?:\.\d+)?)',note['text'])
                    if match:
                        thickness_shown = True
                        yield dict(rule=6,status='pass',title='图面已标明名义板厚',page=page['page'],
                                   reason=f'图面文字“{note["text"]}”给出了名义板厚 {match[1]} mm。',
                                   box=note['box'],objectId=note['id'],evidence={'nominalThicknessMm':float(match[1])})
            if not thickness_shown:
                candidates = [v for v in family if _full_part(v['view'],v['envelope']) and v['envelope'].get('box')]
                if candidates:
                    data = min(candidates, key=lambda v: min(v['envelope']['spansM'].values(), default=float('inf')))
                    yield _result(6,'missing','名义板厚待核对',
                                  '模型含钣金特征。请核对标记视图中的厚度尺寸，或其他视图和技术要求中的等效板厚标注。',
                                  data,data['envelope'])
