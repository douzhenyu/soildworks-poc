"""Audit the geometric targets of drawing dimensions."""

import math
import re
from collections import defaultdict

from dimension_checks import LINEAR_HEADER, REFERENCE_TEXT, _confirmed_span, display_segments, project_model_point
from slot_checks import numbers, same_axis


POSITION = re.compile(r'^DimPosition: ([^,]+),([^,]+),[^\n]+Dangling: (True|False) \| AttachedCount: (\d+) \| AttachedTypes: ([^|\n]+)', re.M)
HEADER = re.compile(r'(.+?) \| SystemValue: ([\d.eE+-]+) \| Type: (\d+) \|')


def _visible_round_target(record, transform, circles, value, kind):
    if kind not in ('5','6') or not transform or transform[12] <= 0:
        return None
    tips = [tip[:2] for raw in re.findall(r'^DimensionArrowHead: (.*)', record, re.M)
            if (tip := numbers(raw, 12))]
    if not tips:
        return None
    radius = value/2 if kind == '6' else value
    matched = {}
    for circle in circles:
        if abs(radius-circle[6]) > 1e-7:
            continue
        center = project_model_point(circle[:3], transform)
        nx, ny, nz = (sum(circle[i+3]*transform[3*i+j] for i in range(3)) for j in range(3))
        if abs(nz) < .05:
            continue
        projected_radius = radius*transform[12]
        for x,y in tips:
            dx,dy = x-center[0],y-center[1]
            dz = -(nx*dx+ny*dy)/nz
            if abs(math.hypot(dx,dy,dz)-projected_radius) <= 1e-5:
                key = tuple(round(z,7) for z in (*center, nx/nz,ny/nz,projected_radius))
                matched[key] = dict(circle=circle, arrowTip=(x,y), sheetCenter=center)
    return next(iter(matched.values())) if len(matched) == 1 else None


def _duplicate_key(record, transform):
    if (not transform or transform[12] <= 0 or not LINEAR_HEADER.match(record)
            or REFERENCE_TEXT.search(record) or 'Parentheses: True' in record):
        return None
    span = _confirmed_span(record, transform)
    if not span:
        return None
    axis, endpoints, _ = span
    if not all(end[2] for end in endpoints):
        return None
    tolerance = record.splitlines()[0].partition(' | ToleranceType: ')[2]
    text = tuple(re.findall(r'^RenderedDimText: (.*?) position=', record, re.M))
    if not text:
        return None
    return (axis, tuple((round(end[0],7),tuple(sorted(end[2]))) for end in endpoints), tolerance, text)


def _thread_targets(record, transform, circles):
    symbols = {int(index): dict(objectId=reference.strip(), name=name.strip())
               for index, reference, name in re.findall(
                   r'^DimensionThreadSymbol: (\d+) \| ([^|\n]*) \| ([^\n]*)', record, re.M)}
    targets = defaultdict(list)
    for index, reference, values in re.findall(
            r'^DimensionThreadModelCircle: (\d+) \| ([^|\n]*) \| ([^\n]*)', record, re.M):
        if (circle := numbers(values, 7)) and circle[6] > 0:
            targets[int(index)].append(dict(method='api_attachment', circle=circle, edgeId=reference.strip()))
    if transform and transform[12] > 0 and circles:
        for index, values in re.findall(r'^DimensionThreadDisplayArc: (\d+) \| (.*)', record, re.M):
            index = int(index)
            arc = numbers(values, 17)
            if not arc or index not in symbols or any(row['method']=='api_attachment' for row in targets[index]):
                continue
            center, normal = arc[10:13], arc[13:16]
            radius = math.dist(arc[4:7], center)
            if radius <= 1e-7 or abs(normal[2]) <= .05:
                continue
            for circle in circles:
                projected = project_model_point(circle[:3], transform)
                axis = [sum(circle[i+3]*transform[3*i+j] for i in range(3)) for j in range(3)]
                if circle[6] <= 0 or math.dist(projected,center[:2]) > 1e-7 or not same_axis(axis,normal):
                    continue
                evidence = dict(method='concentric_visible_edge', circle=circle, sheetCenter=center[:2],
                                displayedRadius=radius)
                if evidence not in targets[index]:
                    targets[index].append(evidence)
    return [dict(index=index, symbol=symbols.get(index), targets=geometry)
            for index, geometry in sorted(targets.items()) if geometry]


def _visible_witness_target(record, transform, lines, index):
    if not lines or not transform or transform[12] <= 0 or not LINEAR_HEADER.match(record):
        return None
    span = _confirmed_span(record, transform)
    if not span:
        return None
    axis = span[0]
    ref_rows = re.findall(r'^DimensionReferencePoint: (.*)', record, re.M)
    point = project_model_point(numbers(ref_rows[index],3),transform)
    if not any(abs(start[axis]-end[axis]) <= 1e-7 and math.dist(start,end) > 1e-7
               and min(math.dist(point,start),math.dist(point,end)) <= 1e-7
               for start,end in display_segments(record)):
        return None
    matches = {}
    other = 1-axis
    for edge in lines:
        start,end = (project_model_point(endpoint,transform) for endpoint in (edge[:3],edge[3:]))
        if (abs(start[axis]-point[axis]) <= 1e-7 and abs(end[axis]-point[axis]) <= 1e-7
                and min(start[other],end[other])+1e-7 < point[other] < max(start[other],end[other])-1e-7):
            key = tuple(sorted((tuple(round(x,7) for x in start),tuple(round(x,7) for x in end))))
            matches.setdefault(key,edge)
    if len(matches) == 1:
        return dict(index=index, sheetPoint=point, modelEdge=next(iter(matches.values())))
    return None


def _centerline_targets(record, transform):
    if not transform or transform[12] <= 0:
        return []
    refs = [numbers(row,3) for row in re.findall(r'^DimensionReferencePoint: (.*)', record, re.M)]
    symbols = {int(index): dict(objectId=reference.strip(), name=name.strip())
               for index, reference, name in re.findall(
                   r'^AttachedCenterline: (\d+) \| ([^|\n]*) \| ([^\n]*)', record, re.M)}
    matches = []
    for raw_index, values in re.findall(r'^AttachedCenterlineDisplayLine: (\d+) \| (.*)', record, re.M):
        index = int(raw_index)
        segment = numbers(values,10)
        if not segment or index not in symbols or index >= len(refs) or not refs[index]:
            continue
        point = project_model_point(refs[index],transform)
        start,end = segment[4:6],segment[7:9]
        if min(math.dist(point,start),math.dist(point,end)) <= 1e-7:
            matches.append(dict(index=index, symbol=symbols[index], sheetPoint=point, line=(start,end)))
    return matches


def dimension_target_evidence(raw):
    for view in re.split(r'^View: ', raw, flags=re.M)[1:]:
        name = view.split(' Model: ',1)[0]
        matrix = re.search(r'^ViewTransform: (.*)', view, re.M)
        transform = numbers(matrix[1],16) if matrix else None
        part_inventory = (re.search(r'^PartViewInventory: True\s*$', view, re.M)
                          and re.search(r'^VisibleEdges: .* Components: 1\s*$', view, re.M)
                          and 'INVENTORY_ERROR:' not in view)
        circles = ([circle for row in re.findall(r'^VisibleFullCircle: (.*)', view, re.M)
                    if (circle := numbers(row,7))] if part_inventory else [])
        lines = ([edge for row in re.findall(r'^VisibleStraightEdge: (.*)', view, re.M)
                  if (edge := numbers(row,6))] if part_inventory else [])
        repeated = defaultdict(list)
        for record in re.split(r'^DIM: ', view, flags=re.M)[1:]:
            header, position = HEADER.match(record), POSITION.search(record)
            if not header:
                continue
            object_id, value, kind = header.groups()
            anchor = numbers(','.join(position.groups()[:2]),2) if position else None
            if not position or anchor is None:
                yield dict(view=name, objectId=object_id, result='unresolved')
                continue
            count = int(position[4])
            direct = sorted({int(index) for index in re.findall(
                r'^IndexedDimension(?:ModelEdge|Vertex|Silhouette): (\d+) \|', record, re.M)})
            sketch = sorted({int(index) for index in re.findall(
                r'^Attached(?:SketchSegment|SketchPoint|CenterMark|Centerline): (\d+) \|', record, re.M)})
            null = sorted({int(index) for index in re.findall(r'^DimensionAttachmentNull: (\d+)', record, re.M)})
            thread = _thread_targets(record,transform,circles) if 'DimensionThread' in record else []
            thread_indices = {target['index'] for target in thread}
            evidence = dict(view=name, objectId=object_id, anchor=anchor, attachmentCount=count,
                            cadGeometryIndices=direct, auxiliaryIndices=sketch, nullIndices=null,
                            apiAttachmentTypes=position[5].strip().rstrip(','))
            if thread:
                evidence['threadTargets'] = thread
            if 'AttachedCenterline:' in record and (centerline := _centerline_targets(record,transform)):
                evidence['centerlineTargets'] = centerline
            if position[3] == 'True':
                result = 'dangling'
            elif count > 0 and all(index in direct for index in range(count)):
                result = 'cad_geometry'
            elif count > 0 and thread_indices and all(
                    index in direct or index in sketch or index in thread_indices for index in range(count)):
                result = 'thread_geometry'
            elif count == 0 and (round_target := _visible_round_target(record,transform,circles,float(value),kind)):
                evidence['roundTarget'] = round_target
                result = 'visible_round_target'
            elif (count == 2 and len(null) == 1 and null[0] in (0,1) and 1-null[0] in direct
                  and (witness := _visible_witness_target(record,transform,lines,null[0]))):
                evidence['witnessTarget'] = witness
                result = 'visible_witness_target'
            elif count > 0 and all(index in direct or index in sketch for index in range(count)):
                result = 'auxiliary_geometry'
            else:
                result = 'unresolved'
            yield dict(evidence,result=result)
            if result == 'cad_geometry' and (key := _duplicate_key(record,transform)):
                repeated[key].append(object_id)
        for group in repeated.values():
            if len(group) > 1:
                yield dict(view=name, result='coincident_dimensions', objectIds=group)
