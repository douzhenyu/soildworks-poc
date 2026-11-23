"""Match straight-slot size dimensions to SolidWorks model topology."""

from collections import defaultdict
import math
import re


TOLERANCE_M = 1e-7


def numbers(text, size):
    try:
        values = tuple(map(float, text.strip().rstrip(',').split(',')))
    except ValueError:
        return None
    return values if len(values) == size and all(map(math.isfinite, values)) else None


def same_point(a, b):
    return math.dist(a, b) <= TOLERANCE_M


def same_line(a, b):
    return ((same_point(a[:3], b[:3]) and same_point(a[3:], b[3:]))
            or (same_point(a[:3], b[3:]) and same_point(a[3:], b[:3])))


def same_circle(a, b):
    axis_dot = sum(x*y for x, y in zip(a[3:6], b[3:6]))
    return (same_point(a[:3], b[:3]) and abs(abs(axis_dot)-1) < 1e-6
            and abs(a[6]-b[6]) <= TOLERANCE_M)


def matches_pair(actual, expected, same):
    return len(actual) == 2 and (
        (same(actual[0], expected[0]) and same(actual[1], expected[1]))
        or (same(actual[0], expected[1]) and same(actual[1], expected[0]))
    )


def same_axis(a, b):
    norm = math.hypot(*a) * math.hypot(*b)
    return norm > 0 and abs(abs(sum(x*y for x, y in zip(a, b)) / norm)-1) < 1e-6


def same_profile(first, second):
    _, size_a, arcs_a, _ = first
    _, size_b, arcs_b, _ = second
    direction_a = [y-x for x, y in zip(arcs_a[0][:3], arcs_a[1][:3])]
    direction_b = [y-x for x, y in zip(arcs_b[0][:3], arcs_b[1][:3])]
    return (same_point(size_a, size_b) and same_axis(direction_a, direction_b)
            and same_axis(arcs_a[0][3:6], arcs_b[0][3:6]))


def visible_openings(view, openings):
    circles = [circle for row in re.findall(r'^VisibleCircle: (.*)', view, re.M)
               if (circle := numbers(row, 7))]
    lines = [line for row in re.findall(r'^VisibleStraightEdge: (.*)', view, re.M)
             if (line := numbers(row, 6))]
    if not circles or not lines or 'INVENTORY_ERROR:' in view:
        return []
    return [opening for opening in openings
            if all(any(same_circle(arc, circle) for circle in circles) for arc in opening[2])
            and all(any(same_line(edge, line) for line in lines) for edge in opening[3])]


def read_openings(view):
    pattern = (r'^ModelStraightSlotOpening: width=([^ ]+) \| length=([^ ]+)'
               r' \| endA=([^|]+)\| endB=([^|]+)\| edges=[^|]+'
               r'\| walls=([^|]+)\| lines=([^\r\n]+)')
    for width, length, end_a, end_b, walls, lines in re.findall(pattern, view, re.M):
        arcs = (numbers(end_a, 7), numbers(end_b, 7))
        edges = tuple(numbers(line, 6) for line in lines.split(';'))
        size = numbers(width+','+length, 2)
        ids = tuple(sorted(walls.strip().split(',')))
        if size and all(arcs) and len(edges) == 2 and all(edges) and len(ids) == 2 and all(ids):
            yield ids, size, arcs, edges


def read_size_binding(record, openings):
    header = re.match(r'(.+?) \| SystemValue: ([\d.eE+-]+) \| Type: (?:2|11|12) \|', record)
    position = re.search(r'^DimPosition: ([^,]+),([^,]+),[^\n]+Dangling: False', record, re.M)
    if not header or not position or 'Parentheses: False' not in record:
        return None
    if re.search(r'^RenderedDimText: [(（]', record, re.M):
        return None
    parts = re.search(r'^DimTextParts: (.*)', record, re.M)
    if not parts:
        return None
    tokens = [token.strip() for token in parts[1].split('|')]
    if len(tokens) != 4 or tokens[0] or tokens[2] or tokens[3]:
        return None
    prefix = re.fullmatch(r'(\d+)\s*[-×xX*]', tokens[1])
    if tokens[1] and not prefix:
        return None
    quantity = int(prefix[1]) if prefix else 1
    if quantity < 1:
        return None
    edges = [numbers(line, 6) for line in re.findall(r'^DimensionStraightEdge: (.*)', record, re.M)]
    arcs = [numbers(line, 7) for line in re.findall(r'^DimensionCircularEdge: [^|]*\| (.*)', record, re.M)]
    value = float(header[2])
    # The model dimension must also be displayed with the same quantity and size.
    rendered = re.findall(r'^RenderedDimText: (.*?) position=', record, re.M)
    if len(rendered) != 1:
        return None
    printed = re.fullmatch(r'(?:(\d+)\s*[-×xX*]\s*)?(\d+(?:\.\d+)?)', rendered[0].strip())
    if (not printed or int(printed[1] or 1) != quantity
            or abs(float(printed[2])/1000-value) > TOLERANCE_M):
        return None
    candidates = {}
    for slot_id, (width, length), slot_arcs, slot_edges in openings:
        if abs(value-width) <= TOLERANCE_M and all(edges) and not arcs and matches_pair(edges, slot_edges, same_line):
            candidates[(slot_id, 'width')] = width
        if abs(value-length) <= TOLERANCE_M and all(arcs) and not edges and matches_pair(arcs, slot_arcs, same_circle):
            candidates[(slot_id, 'length')] = length
    if len(candidates) != 1:
        return None
    (slot_id, kind), measured = next(iter(candidates.items()))
    return slot_id, kind, measured, quantity, header[1], tuple(map(float, position.groups()))


def slot_views(pages):
    inventories = defaultdict(list)
    views = []
    for page_no, page in enumerate(pages, 1):
        for view in re.split(r'^View: ', page['raw'], flags=re.M)[1:]:
            title = view.splitlines()[0]
            config = re.search(r'^ViewMetadata: .*? \| configuration=([^\r\n]*)', view, re.M)
            if ' Model: ' not in title or not config or not re.search(r'^PartViewInventory: True\s*$', view, re.M):
                continue
            view_name, path = title.split(' Model: ', 1)
            key = (path, config[1])
            inventories[key].extend(read_openings(view))
            if re.search(r'^DrawingUnits: 0,', page['raw'], re.M):
                views.append((page_no, view_name, key, view))
    return inventories, views


def inspect_slots(pages):
    """Group by shared cylindrical walls and keep per-dimension binding evidence."""
    inventories, views = slot_views(pages)
    sizes = {}
    for key, openings in inventories.items():
        for walls, size, _, _ in openings:
            sizes[key, walls] = size
    bindings = []
    view_index = {(page_no, name, key): view for page_no, name, key, view in views}
    for page_no, name, key, view in views:
        openings = inventories[key]
        visible = visible_openings(view, openings)
        parent = re.search(r'^DetailBaseView: ([^\r\n]+)', view, re.M)
        parent_view = view_index.get((page_no, parent[1], key)) if parent else None
        parent_visible = visible_openings(parent_view, openings) if parent_view else []
        for record in re.split(r'^DIM: ', view, flags=re.M)[1:]:
            bound = read_size_binding(record, openings)
            if bound is None:
                continue
            walls, kind, value, quantity, dimension, anchor = bound
            width, length = sizes[key, walls]
            same_size = sum(
                model == key and abs(w-width) <= TOLERANCE_M and abs(l-length) <= TOLERANCE_M
                for (model, _), (w, l) in sizes.items()
            )
            seed = next((opening for opening in visible if opening[0] == walls), None)
            visible_ids = sorted({','.join(opening[0]) for opening in visible
                                  if seed is not None and same_profile(seed, opening)})
            parent_ids = sorted({','.join(opening[0]) for opening in parent_visible
                                 if seed is not None and same_profile(seed, opening)})
            bindings.append(dict(page=page_no, view=name, model=key[0], configuration=key[1],
                                 objectId=dimension, slotId=','.join(walls), axis=kind,
                                 valueMm=round(value*1000, 6), quantity=quantity,
                                 modelRecognizedSameSize=same_size, visibleSlotIds=visible_ids,
                                 baseView=parent[1] if parent_view else None, baseVisibleSlotIds=parent_ids,
                                 anchor=anchor))
    return len(sizes), bindings


def slot_size_pairs(bindings):
    """Find explicit width and total length on the same identified slot."""
    groups = defaultdict(lambda: defaultdict(list))
    for binding in bindings:
        key = tuple(binding[field] for field in ('model', 'configuration', 'slotId'))
        groups[key][binding['axis']].append(binding)
    pairs = []
    for axes in groups.values():
        for width in axes['width']:
            for length in axes['length']:
                if width['quantity'] != length['quantity']:
                    continue
                quantity = width['quantity']
                visible_ids = width['visibleSlotIds']
                visible_match = (len(visible_ids) == quantity and visible_ids == length['visibleSlotIds'])
                base_ids = width['baseVisibleSlotIds']
                base_match = (width['page'] == length['page'] and width['view'] == length['view']
                              and width['baseView'] and width['baseView'] == length['baseView']
                              and len(base_ids) == quantity and base_ids == length['baseVisibleSlotIds']
                              and visible_ids and visible_ids == length['visibleSlotIds']
                              and set(visible_ids) < set(base_ids))
                if quantity == 1:
                    scope = 'single_slot'
                elif visible_match:
                    scope = 'matched_visible_set'
                elif base_match:
                    scope = 'matched_base_visible_set'
                else:
                    scope = 'anchor_slot_only'
                pairs.append(dict(width=width, length=length, scope=scope))
    return pairs
