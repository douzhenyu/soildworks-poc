"""Verify repeated round-hole callouts against SolidWorks model and view geometry."""

import math
import re
from collections import defaultdict

from dimension_checks import project_model_point
from slot_checks import numbers, same_axis, same_circle
from wizard_hole_checks import read_wizard_holes, wizard_group_evidence


QUANTITY = re.compile(r'(\d+)\s*[-xX×*]\s*(?=<MOD-DIAM>|Ø|⌀|M)', re.I)
PLAIN_DIAMETER = re.compile(
    r'(\d+)\s*[-xX×*]\s*(?:<MOD-DIAM>|Ø|⌀)\s*(\d+(?:\.\d+)?)?'
    r'\s*(?:贯穿|完全贯穿|通孔|\([通]\)|（通）)?')


def _printed_diameter(record, quantity, diameter):
    tokens = re.findall(r'^RenderedDimText: (.*?) position=', record, re.M)
    text = ''.join(tokens).replace(' ', '').replace('　', '')
    printed = re.fullmatch(r'(\d+)[-xX×*](?:<MOD-DIAM>|Ø|⌀)(\d+(?:\.\d+)?)'
                           r'(?:贯穿|完全贯穿|通孔|\(通\)|（通）|<HOLE-DEPTH>\d+(?:\.\d+)?)?', text)
    return bool(printed and int(printed[1]) == quantity and abs(float(printed[2])-diameter) <= .01)


def _round_group(view, openings, wall, bound_circle):
    seed = next((item for item in openings if item['wallId'] == wall and
                 same_circle(item['circle'],bound_circle)), None)
    if not seed or not seed['featureId']:
        return None
    if not re.search(r'^VisibleEdges: .* Components: 1\s*$', view, re.M) or 'INVENTORY_ERROR:' in view:
        return None
    matrix = re.search(r'^ViewTransform: (.*)', view, re.M)
    transform = numbers(matrix[1],16) if matrix else None
    if not transform or transform[12] <= 0:
        return None
    visible = [circle for row in re.findall(r'^VisibleFullCircle: (.*)', view, re.M)
               if (circle := numbers(row,7))]
    if not any(same_circle(bound_circle,circle) for circle in visible):
        return None
    candidates = [item for item in openings
                  if item['featureId'] == seed['featureId']
                  and abs(item['circle'][6]-bound_circle[6]) <= 1e-7
                  and same_axis(item['circle'][3:6],bound_circle[3:6])]
    mouths = defaultdict(list)
    for item in candidates:
        circle = item['circle']
        if any(same_circle(circle, drawn) for drawn in visible):
            center = project_model_point(circle[:3],transform)
            mouths[item['wallId']].append(dict(center=center, modelCenter=circle[:3], edgeId=item['edgeId']))
    if wall not in mouths:
        return None
    # Distinct walls projected to the same center may represent stacked or stepped geometry.
    accepted = []
    for wall_id, entries in mouths.items():
        first = entries[0]
        if (any(math.dist(first['center'],other['center']) > 1e-7 for other in entries)
                or any(math.dist(first['center'],other['center']) <= 1e-7 for other in accepted)):
            return None
        accepted.append(dict(first, wallId=wall_id))
    return dict(featureName=seed['feature'], featureId=seed['featureId'], featureType=seed['featureType'],
                visibleHoleCount=len(accepted),
                modelFeatureWallCount=len({item['wallId'] for item in candidates}), visibleHoles=accepted)


def _inventories(view):
    openings = []
    rims = defaultdict(list)
    for wall,row in re.findall(r'^ModelCircularWallRim: (\S+) \| (.*)',view,re.M):
        if circle := numbers(row,7):
            rims[wall].append(circle)
    bore_walls = []
    for wall,row,body,owner,kind in re.findall(
            r'^ModelBoreWall: (\S+) \| ([^|\n]+) \| body=([^|\n]*) \| owner=([^|\n]*) \| type=([^\n]*)',
            view,re.M):
        if axis := numbers(row,7):
            bore_walls.append(dict(wallId=wall,bodyId=body,featureId=owner,featureType=kind.strip(),
                                   cylinder=axis,rims=rims[wall]))
    if 'ModelCircularOpeningInventoryEnd' in view:
        for edge, wall, row, owner in re.findall(
                r'^ModelCircularOpening: (\S+) \| (\S+) \| ([^|\n]+) \| ([^\n]*)', view, re.M):
            if (circle := numbers(row,7)):
                name, _, metadata = owner.strip().partition(' | owner=')
                feature_id, _, feature_type = metadata.partition(' | type=')
                openings.append(dict(edgeId=edge,wallId=wall,circle=circle,feature=name,
                                     featureId=feature_id,featureType=feature_type,rims=rims[wall]))
    cylinders = [circle[6]*2000 for row in re.findall(r'^ModelInwardCylinder: (.*)', view, re.M)
                 if (circle := numbers(row,7))]
    pattern_seeds = defaultdict(set)
    for pattern, seed, kind in re.findall(r'^ModelPatternSeed: (\S+) \| (\S+) \| (\w+)', view, re.M):
        pattern_seeds[pattern,kind].add(seed)
    return cylinders, openings, pattern_seeds, list(read_wizard_holes(view)), bore_walls


def _pattern_group(openings, pattern_seeds, group, bound_circle):
    owner = group['featureId']
    families = [(pattern,kind,next(iter(seeds))) for (pattern,kind),seeds in pattern_seeds.items()
                if len(seeds) == 1 and (owner == pattern or owner in seeds)]
    if len(families) != 1:
        return None
    pattern, kind, seed = families[0]
    holes = {}
    axis = bound_circle[3:6]
    for item in openings:
        circle = item['circle']
        if (item['featureId'] not in (pattern,seed) or abs(circle[6]-bound_circle[6]) > 1e-7
                or not same_axis(axis,circle[3:6])):
            continue
        center = circle[:3]
        axial = sum(x*y for x,y in zip(center,axis))
        transverse = tuple(x-axial*y for x,y in zip(center,axis))
        wall = item['wallId']
        if wall in holes:
            if math.dist(holes[wall]['axisPosition'],transverse) > 1e-7:
                return None
            continue
        if any(math.dist(transverse,hole['axisPosition']) <= 1e-7 for hole in holes.values()):
            return None
        holes[wall] = dict(wallId=wall,featureId=item['featureId'],axisPosition=transverse,modelCenter=center)
    if {hole['featureId'] for hole in holes.values()} != {pattern,seed}:
        return None
    return dict(patternId=pattern, patternType=kind, seedFeatureId=seed, patternHoles=list(holes.values()),
                patternHoleCount=len(holes))


def repeated_hole_counts(raw):
    """Report visible group evidence, model upper bounds and unsupported round callouts."""
    if not re.search(r'^DrawingUnits: 0,', raw, re.M):
        return
    views = re.split(r'^View: ', raw, flags=re.M)[1:]
    inventories = {}
    for view in views:
        title = view.splitlines()[0]
        meta = re.search(r'^ViewMetadata: .*? \| configuration=([^\r\n]*)', view, re.M)
        if ' Model: ' not in title or not meta or 'PartViewInventory: True' not in view:
            continue
        view_name, model_path = title.split(' Model: ', 1)
        key = (model_path, meta[1])
        if 'ModelCylinderInventoryEnd' in view:
            inventories[key] = _inventories(view)
        cylinders, openings, pattern_seeds, wizards, bore_walls = inventories.get(key, ([], [], {}, [], []))
        for record in re.split(r'^DIM: ', view, flags=re.M)[1:]:
            label = re.search(r'^DimTextParts: (.*)', record, re.M)
            position = re.search(r'^DimPosition: ([^,]+),([^,]+),[^\n]+Dangling: False', record, re.M)
            if not label or not position or 'Parentheses: True' in record:
                continue
            parts = [value.strip() for value in label[1].split('|')]
            if len(parts) != 4:
                continue
            quantity = next((m for part in (parts[1],parts[3]) if (m := QUANTITY.match(part))), None)
            if not quantity or int(quantity[1]) < 2:
                continue
            count = int(quantity[1])
            claim = dict(view=view_name,objectId=record.split(' | ',1)[0],
                         anchor=tuple(map(float,position.groups())),quantity=count,
                         result='unverified_round_claim',callout=' '.join(part for part in parts if part))
            wizard = wizard_group_evidence(record,view,count,wizards,openings,bore_walls)
            if wizard:
                yield dict(claim,modelPath=model_path,configuration=meta[1],**wizard)
                continue
            match = PLAIN_DIAMETER.fullmatch(parts[1])
            depth = re.fullmatch(r'<HOLE-DEPTH>\s*\d+(?:\.\d+)?', parts[2])
            if not match or parts[0] or parts[3] or (parts[2] and not depth and parts[2] not in ('(通)','（通）','贯穿','完全贯穿','通孔')):
                yield dict(claim,missing=['supported_callout_scope'])
                continue
            native = re.search(r'\| SystemValue: ([\d.eE+-]+) \| Type: (?:6|15) \|', record)
            diameter = float(match[2]) if match[2] else float(native[1])*1000 if native else None
            if diameter is None or not _printed_diameter(record,count,diameter):
                yield dict(claim,missing=['displayed_diameter'])
                continue
            bindings = [(wall,circle) for wall,row in re.findall(
                r'^DimensionCircularOpening: (\S+) \| \S+ \| (.*)', record, re.M)
                        if (circle := numbers(row,7)) and abs(circle[6]*2000-diameter) <= .01]
            if len({wall for wall,_ in bindings}) != 1 or not cylinders:
                yield dict(claim,diameterMm=diameter,missing=['bound_round_opening_or_model_inventory'])
                continue
            wall, circle = bindings[0]
            upper = sum(abs(value-diameter) <= .01 for value in cylinders)
            group = _round_group(view,openings,wall,circle)
            claim.update(diameterMm=diameter,modelUpperBound=upper,seedWallId=wall)
            if group:
                claim.update(group)
            pattern = _pattern_group(openings,pattern_seeds,group,circle) if group else None
            if pattern:
                claim.update(pattern)
            if upper and count > upper:
                result = 'exceeds_bound'
            elif group and group['visibleHoleCount'] == count:
                result = 'matched_visible_feature_group'
            elif pattern and pattern['patternHoleCount'] == count:
                result = 'matched_pattern_group'
            elif upper:
                result = 'within_bound'
            else:
                result = 'unverified_round_claim'
            yield dict(claim,result=result)
