"""Checks based on the drawing geometry returned by SolidWorks."""

import re
from math import dist
from collections import defaultdict, deque
from slot_checks import numbers


LINEAR_HEADER = re.compile(r'(.+?) \| SystemValue: ([\d.eE+-]+) \| Type: (2|11|12) \|')
REFERENCE_TEXT = re.compile(r'^RenderedDimText: \s*(?:\d+\s*[×xX*-]\s*)?[(（]', re.M)


def project_model_point(point, transform):
    return tuple(transform[9+axis] + transform[12] *
                 sum(point[i]*transform[3*i+axis] for i in range(3))
                 for axis in range(2))


def display_segments(record):
    for raw in re.findall(r'^DimensionDisplayLine: (.+)$', record, re.M):
        try:
            values = [float(value) for value in raw.rstrip(', \r').split(',')]
        except ValueError:
            continue
        if len(values) == 10:
            yield values[4:6], values[7:9]


def _linear_constraint(record, scale):
    header = LINEAR_HEADER.match(record)
    position = re.search(r'^DimPosition: ([^,]+),([^,]+),[^\n]+Dangling: False', record, re.M)
    if not header or not position or 'Parentheses: False' not in record:
        return None
    if REFERENCE_TEXT.search(record):
        return None
    lines = list(display_segments(record))
    anchor = tuple(map(float, position.groups()))
    expected = float(header[2]) * scale
    tolerance = .00001
    if expected < 10*tolerance:
        return None
    for axis in (0, 1):
        other = 1-axis
        main_lines = [(start, end) for start, end in lines
                      if abs(start[other]-anchor[other]) < tolerance
                      and abs(end[other]-anchor[other]) < tolerance
                      and abs(start[axis]-end[axis]) > tolerance]
        if not main_lines:
            continue
        ticks = sorted({round(start[axis], 5) for start, end in lines
                        if abs(start[axis]-end[axis]) < tolerance
                        and abs(start[other]-end[other]) > tolerance
                        and min(start[other],end[other])-.00055 <= anchor[other]
                        <= max(start[other],end[other])+.00055})
        if len(ticks) == 2 and abs(ticks[1]-ticks[0]-expected) < 2*tolerance:
            return axis, ticks, anchor, header[1], float(header[2])*1000
    return None


def _endpoint_ids(record):
    ids = defaultdict(set)
    for index, scope, key in re.findall(r'^AttachmentPersistentReference: ([01]) \| (model|drawing) \| ([^\s]+)', record, re.M):
        ids[int(index)].add((scope, key))
    return ids


def _axis_at_witnesses(record, points, anchor, expected):
    lines = list(display_segments(record))
    axes = []
    tolerance = 1e-7
    for axis in (0, 1):
        other = 1-axis
        start, end = sorted(point[axis] for point in points)
        if end-start <= 10*tolerance or abs(end-start-expected) > tolerance:
            continue
        witness_positions = [first[axis] for first, second in lines
                             if abs(first[axis]-second[axis]) <= tolerance
                             and abs(first[other]-second[other]) > tolerance
                             and min(first[other],second[other])-.00055 <= anchor[other]
                             <= max(first[other],second[other])+.00055]
        if all(any(abs(tick-position) <= tolerance for tick in witness_positions)
               for position in (start,end)):
            axes.append(axis)
    return axes[0] if len(axes) == 1 else None


def _confirmed_span(record, transform):
    position = re.search(r'^DimPosition: ([^,]+),([^,]+),[^\n]+Dangling: False \| AttachedCount: ([12]) \|', record, re.M)
    if not position or 'Parentheses: False' not in record:
        return None
    refs = [numbers(row, 3) for row in re.findall(r'^DimensionReferencePoint: (.*)', record, re.M)[:2]]
    if len(refs) != 2 or not all(refs):
        return None
    points = [project_model_point(ref, transform) for ref in refs]
    if position[3] == '1':
        edge_row = re.search(r'^IndexedDimensionStraightEdge: 0 \| (.*)', record, re.M)
        edge = numbers(edge_row[1], 6) if edge_row else None
        if not edge:
            return None
        edge_points = sorted(project_model_point(endpoint, transform) for endpoint in (edge[:3],edge[3:]))
        if any(dist(p,q) > 1e-7 for p,q in zip(sorted(points),edge_points)):
            return None
    name, system_value, _ = LINEAR_HEADER.match(record).groups()
    anchor = tuple(map(float, position.groups()[:2]))
    expected = float(system_value)*transform[12]
    axis = _axis_at_witnesses(record, points, anchor, expected)
    if axis is None:
        return None
    ids = _endpoint_ids(record)
    ends = sorted((point[axis], ref, ids[i] if position[3] == '2' else set())
                  for i, (point, ref) in enumerate(zip(points,refs)))
    return axis, ends, dict(objectId=name, anchor=anchor, valueMm=round(float(system_value)*1000, 8),
                            modelReferencePoints=[end[1] for end in ends],
                            projectedSpan=[end[0] for end in ends])


def _end_nodes(spans):
    nodes = []
    tolerance = 1e-7
    for ends, evidence in spans:
        pair = []
        for coordinate, point, ids in ends:
            matching = [i for i, members in enumerate(nodes)
                        if any(abs(coordinate-x) <= tolerance and (dist(point,p) <= tolerance or ids & keys)
                               for x, p, keys in members)]
            if len(matching) > 1:
                break
            if matching:
                node = matching[0]
                nodes[node].append((coordinate, point, ids))
            else:
                node = len(nodes)
                nodes.append([(coordinate, point, ids)])
            pair.append(node)
        if len(pair) == 2 and pair[0] != pair[1]:
            yield (*pair, evidence)


def _closing_path(graph, start, end, total):
    lower, upper = total['projectedSpan']
    queue = deque([(start, [])])
    seen = {start}
    while queue:
        node, path = queue.popleft()
        if node == end:
            return path
        for neighbor, evidence in graph[node]:
            lo, hi = evidence['projectedSpan']
            if neighbor in seen or lo < lower-1e-7 or hi > upper+1e-7:
                continue
            if hi-lo >= upper-lower-1e-7:
                continue
            seen.add(neighbor)
            queue.append((neighbor, path + [evidence]))
    return []


def linear_chain_evidence(raw):
    """Audit same-view horizontal and vertical chains using projected CAD references."""
    checks = []
    loops = []
    for view in re.split(r'^View: ', raw, flags=re.M)[1:]:
        matrix = re.search(r'^ViewTransform: (.*)', view, re.M)
        transform = numbers(matrix[1], 16) if matrix else None
        spans = [[], []]
        view_name = view.split(' Model: ', 1)[0]
        for record in re.split(r'^DIM: ', view, flags=re.M)[1:]:
            header = LINEAR_HEADER.match(record)
            if not header:
                continue
            check = dict(view=view_name, objectId=header[1], result='unverified_span')
            if 'Parentheses: True' in record or REFERENCE_TEXT.search(record):
                check['result'] = 'reference_excluded'
            elif transform and transform[12] > 0 and (span := _confirmed_span(record, transform)):
                axis, ends, evidence = span
                spans[axis].append((ends,evidence))
                check.update(evidence, axis='x' if axis == 0 else 'y', result='verified_span')
            checks.append(check)
        for axis, candidates in enumerate(spans):
            graph = defaultdict(list)
            edges = list(_end_nodes(candidates))
            for start, end, evidence in edges:
                graph[start].append((end,evidence))
            for start, end, evidence in edges:
                path = _closing_path(graph,start,end,evidence)
                if len(path) >= 2:
                    loops.append(dict(view=view_name, axis='x' if axis == 0 else 'y',
                                      total=evidence, segments=path))
    return checks, loops
