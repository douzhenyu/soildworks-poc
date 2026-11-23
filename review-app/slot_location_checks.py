"""Verify location evidence from attached geometry and printed witness lines."""

from collections import defaultdict, deque
import math
import re

from dimension_checks import _linear_constraint, project_model_point
from slot_checks import numbers, same_line, slot_views, visible_openings


SHEET_TOLERANCE = 1e-7


def _sketch_to_model(point, transform):
    return tuple(sum((point[j]-transform[9+j])*transform[3*i+j] for j in range(3))
                 / transform[12] for i in range(3))


def _indexed_vectors(record, label, length):
    return {int(index): value
            for index, row in re.findall(rf'^{label}: (\d+) \| (.*)', record, re.M)
            if (value := numbers(row, length))}


def _on_perpendicular(point, line, axis):
    start, end = line
    other = 1-axis
    return (abs(start[axis]-end[axis]) <= SHEET_TOLERANCE
            and abs(start[axis]-point[axis]) <= SHEET_TOLERANCE
            and abs(start[other]-end[other]) > SHEET_TOLERANCE
            and min(start[other], end[other])-SHEET_TOLERANCE <= point[other]
            <= max(start[other], end[other])+SHEET_TOLERANCE)


def _datum_key(edge):
    return tuple(sorted((edge[:3], edge[3:])))


def _location_links(view, transform, centers, visible_edges, slot_edges):
    for record in re.split(r'^DIM: ', view, flags=re.M)[1:]:
        constraint = _linear_constraint(record, transform[12])
        if constraint is None or not re.search(r'AttachedCount: 2 \|', record):
            continue
        axis, ticks, anchor, name, value = constraint
        printed = re.findall(r'^RenderedDimText: (.*?) position=', record, re.M)
        if (len(printed) != 1 or not re.fullmatch(r'\d+(?:\.\d+)?', printed[0].strip())
                or abs(float(printed[0])-value) > .0001):
            continue
        refs = [numbers(row, 3) for row in re.findall(r'^DimensionReferencePoint: (.*)', record, re.M)]
        if len(refs) < 2 or not all(refs[:2]):
            continue
        points = [project_model_point(ref, transform) for ref in refs[:2]]
        if any(abs(tick-point) > .000016 for tick, point in
               zip(ticks, sorted(point[axis] for point in points))):
            continue
        sketches = _indexed_vectors(record, 'AttachedSketchLine', 6)
        sketch_transforms = _indexed_vectors(record, 'AttachedSketchTransform', 16)
        edges = _indexed_vectors(record, 'IndexedDimensionStraightEdge', 6)
        nodes = []
        attachments = []
        for index, point in enumerate(points):
            sketch = sketches.get(index)
            sketch_transform = sketch_transforms.get(index)
            edge = edges.get(index)
            if sketch and sketch_transform and sketch_transform[12] > 0:
                line = tuple(project_model_point(_sketch_to_model(endpoint, sketch_transform), transform)
                             for endpoint in (sketch[:3], sketch[3:]))
                if not _on_perpendicular(point, line, axis):
                    break
                matched = [slot for slot, center in centers.items()
                           if _on_perpendicular(center, line, axis)]
                if len(matched) != 1:
                    break
                nodes.append(('slot', matched[0]))
                attachments.append(dict(index=index, slotId=matched[0], sketchLine=line))
            elif (edge and any(same_line(edge, candidate) for candidate in visible_edges)
                  and not any(same_line(edge, candidate) for candidate in slot_edges)):
                line = (project_model_point(edge[:3], transform), project_model_point(edge[3:], transform))
                if not _on_perpendicular(point, line, axis):
                    break
                nodes.append(('edge', _datum_key(edge)))
                attachments.append(dict(index=index, modelEdge=nodes[-1][1]))
            else:
                break
        if len(nodes) == 2 and nodes[0] != nodes[1] and any(node[0] == 'slot' for node in nodes):
            yield axis, nodes, dict(objectId=name, anchor=anchor, valueMm=round(value, 6),
                                    referencePoints=points, attachments=attachments)


def _edge_path(graph, start):
    pending = deque([(start, [])])
    seen = {start}
    while pending:
        node, path = pending.popleft()
        if node[0] == 'edge':
            return node[1], path
        for neighbor, evidence in graph[node]:
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append((neighbor, path + [evidence]))
    return None, []


def slot_location_evidence(pages):
    inventories, views = slot_views(pages)
    for page, view_name, key, view in views:
        openings = inventories[key]
        if not openings:
            continue
        matrix = re.search(r'^ViewTransform: (.*)', view, re.M)
        transform = numbers(matrix[1], 16) if matrix else None
        if not transform or transform[12] <= 0:
            continue
        visible = visible_openings(view, openings)
        centers = {}
        for walls, _, arcs, _ in visible:
            depth = sum(arcs[0][3+i]*transform[3*i+2] for i in range(3))
            if abs(abs(depth)-1) <= 1e-6:
                midpoint = tuple((arcs[0][i]+arcs[1][i])/2 for i in range(3))
                centers[','.join(walls)] = project_model_point(midpoint, transform)
        if not centers:
            continue
        visible_edges = [edge for row in re.findall(r'^VisibleStraightEdge: (.*)', view, re.M)
                         if (edge := numbers(row, 6))]
        slot_edges = [edge for opening in visible for edge in opening[3]]
        graphs = [defaultdict(list), defaultdict(list)]
        for axis, (first, second), evidence in _location_links(view, transform, centers,
                                                               visible_edges, slot_edges):
            graphs[axis][first].append((second, evidence))
            graphs[axis][second].append((first, evidence))
        for slot, center in centers.items():
            for axis, graph in enumerate(graphs):
                start = ('slot', slot)
                if start not in graph:
                    continue
                edge, path = _edge_path(graph, start)
                if not path:
                    path = [evidence for _, evidence in graph[start]]
                yield dict(page=page, view=view_name, feature='straight_slot',
                           model=key[0], configuration=key[1], slotId=slot,
                           axis='x' if axis == 0 else 'y', center=center,
                           result='located_from_model_edge' if edge else 'relative_spacing_only',
                           modelEdge=edge, dimensions=path)
