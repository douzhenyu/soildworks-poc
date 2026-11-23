"""Match compound and threaded drawing callouts to Hole Wizard features."""

import math
import re
from collections import defaultdict

from dimension_checks import project_model_point
from dimension_target_checks import _thread_targets
from slot_checks import numbers, same_axis, same_circle


PARAMETERS = ('holeDiameter','diameter','throughDiameter','tapDrillDiameter','threadDiameter',
              'counterboreDiameter','countersinkDiameter','holeDepth','depth','threadDepth','counterboreDepth')
MM = r'(\d+(?:\.\d+)?)'
DIAMETER = r'(?:<MOD-DIAM>|Ø|⌀)'
PREFIX = r'(\d+)[-xX×*]'
THREAD = re.compile(PREFIX+r'M'+MM+r'(贯穿|完全贯穿|通孔|<HOLE-DEPTH>'+MM+r')?', re.I)
COUNTERBORE = re.compile(PREFIX+DIAMETER+MM+r'(?:贯穿|完全贯穿|通孔)<HOLE-SPOT>'+DIAMETER+
                         MM+r'<HOLE-DEPTH>'+MM+r'(?:/背面)?')


def read_wizard_holes(view):
    points = defaultdict(list)
    for feature, value in re.findall(r'^ModelWizardHolePoint: (\S+) \| [^|\n]+ \| ([^\n]+)',view,re.M):
        if point := numbers(value,3):
            points[feature].append(point)
    parameters = {key: dict(zip(PARAMETERS,values)) for key,row in re.findall(
        r'^ModelWizardHoleParameters: (\S+) \| ([^\n]+)',view,re.M) if (values := numbers(row,len(PARAMETERS)))}
    for key,name,kind,size,count,end in re.findall(
            r'^ModelWizardHole: (\S+) \| ([^|\n]*) \| type=(\d+) \| size=([^|\n]*) \| points=(\d+) \| end=(\d+)',view,re.M):
        if int(count) > 0 and len(set(points[key])) == int(count) and key in parameters:
            yield dict(featureId=key,featureName=name,featureType=int(kind),fastenerSize=size.strip(),
                       holeCenters=points[key],endCondition=int(end),parameters=parameters[key])


def _mm_equal(metres, millimetres):
    return abs(metres*1000-millimetres) <= .01


def _align(point, circle, depth=None):
    delta = tuple(x-y for x,y in zip(point,circle[:3]))
    along = sum(x*y for x,y in zip(delta,circle[3:6]))
    normal = math.hypot(*(x-along*y for x,y in zip(delta,circle[3:6])))
    return normal <= 1e-7 and (depth is None or abs(along) <= depth+1e-6)


def _bore_owners(circle, bore_walls):
    return [wall for wall in bore_walls if abs(wall['cylinder'][6]-circle[6]) <= 1e-7
            and any(same_circle(circle,rim) for rim in wall['rims'])]


def _bore_stages(center, circle, wizard, bore_walls, depth):
    axis = circle[3:6]
    stages = []
    for wall in bore_walls:
        if wall['featureId'] != wizard['featureId'] or not same_axis(axis,wall['cylinder'][3:6]):
            continue
        rims = [rim for rim in wall['rims'] if abs(rim[6]-wall['cylinder'][6]) <= 1e-7
                and same_axis(axis,rim[3:6]) and _align(center,rim)]
        if not rims or not any(_align(center,rim,depth) for rim in rims):
            continue
        if sum(any(_align(point,rim,depth) for rim in rims) for point in wizard['holeCenters']) != 1:
            continue
        stations = sorted({round(sum((x-y)*direction for x,y,direction in zip(rim[:3],center,axis))*1000,4)
                           for rim in rims})
        stage = dict(wallId=wall['wallId'],bodyId=wall['bodyId'],
                     diameterMm=round(wall['cylinder'][6]*2000,4),axialOffsetsMm=stations)
        if len(stations) == 2:
            stage['cylindricalLengthMm'] = round(stations[1]-stations[0],4)
        stages.append(stage)
    return sorted(stages,key=lambda stage: (stage['axialOffsetsMm'],stage['diameterMm']))


def _point_group(wizard, seed, visible, owned, transform, depth, bore_walls):
    candidates = [circle for circle in visible if abs(circle[6]-seed[6]) <= 1e-7
                  and same_axis(circle[3:6],seed[3:6])]
    result = []
    for center in wizard['holeCenters']:
        projected = project_model_point(center,transform)
        if any(math.dist(projected,hole['sheetCenter']) <= 1e-7 for hole in result):
            return None
        supported = [(circle, _bore_owners(circle,bore_walls)) for circle in candidates
                     if _align(center,circle,None if any(same_circle(circle,c) for c in owned) else depth)]
        identified = [(circle,walls[0]) for circle,walls in supported
                      if len(walls) == 1 and walls[0]['featureId'] == wizard['featureId']]
        drawn = identified[0][0] if identified else supported[0][0] if supported else None
        if drawn is None:
            return None
        hole = dict(modelCenter=center,sheetCenter=projected,visibleCircle=drawn,axisDirection=drawn[3:6])
        if len({wall['wallId'] for _,wall in identified}) == 1:
            wall = identified[0][1]
            hole.update(wallId=wall['wallId'],bodyId=wall['bodyId'],association='wizard_owned_wall')
        else:
            hole['association'] = 'visible_coaxial_circle'
        if stages := _bore_stages(center,drawn,wizard,bore_walls,depth):
            hole['boreStages'] = stages
        result.append(hole)
    return result


def _blind_cylinder_depths(wizard, holes, openings):
    depths = []
    for hole in holes:
        circle = hole['visibleCircle']
        matched = {item['wallId']: item for item in openings if item['featureId'] == wizard['featureId']
                   and same_circle(circle,item['circle'])}
        if len(matched) != 1:
            return []
        wall = next(iter(matched.values()))
        axis = circle[3:6]
        rims = [rim for rim in wall['rims'] if abs(rim[6]-circle[6]) <= 1e-7
                and same_axis(axis,rim[3:6]) and _align(rim[:3],circle)]
        if not any(same_circle(rim,circle) for rim in rims):
            return []
        stations = sorted({round(sum(x*y for x,y in zip(rim[:3],axis)),7) for rim in rims})
        if len(stations) != 2 or stations[1]-stations[0] <= 1e-7:
            return []
        depths.append(dict(wallId=wall['wallId'],cylinderDepthMm=round((stations[1]-stations[0])*1000,4)))
    return depths


def _parse_callout(record, quantity):
    tokens = re.findall(r'^RenderedDimText: (.*?) position=',record,re.M)
    text = re.sub(r'\s+','',''.join(tokens))
    if (match := THREAD.fullmatch(text)) and int(match[1]) == quantity:
        return 'thread',float(match[2]),None,float(match[4]) if match[4] else None,bool(match[3] and not match[4])
    if (match := COUNTERBORE.fullmatch(text)) and int(match[1]) == quantity:
        return 'counterbore',float(match[2]),float(match[3]),float(match[4]),True
    head = re.fullmatch(PREFIX+DIAMETER+MM, re.sub(r'\s+','',''.join(tokens[:3])))
    if head and int(head[1]) == quantity and len(tokens) >= 5 and tokens[-2].strip() == '<HOLE-DEPTH>':
        tol = re.search(r'\| ToleranceType: ([1-9]\d*) \|',record)
        if re.fullmatch(MM,tokens[-1].strip()) and (len(tokens) == 5 or (
                tol and all(re.fullmatch(r'[+−-]?\d*(?:\.\d+)?', token.strip()) for token in tokens[3:-2]))):
            return 'blind_diameter',float(head[2]),None,float(tokens[-1]),False
    return None


def wizard_group_evidence(record, view, quantity, wizards, openings, bore_walls=()):
    """Verify one uniquely bound wizard feature, its visible centers and its callout."""
    callout = _parse_callout(record,quantity)
    if not callout or not wizards or not re.search(r'^VisibleEdges: .* Components: 1\s*$',view,re.M):
        return None
    matrix = re.search(r'^ViewTransform: (.*)',view,re.M)
    transform = numbers(matrix[1],16) if matrix else None
    if not transform or transform[12] <= 0 or 'INVENTORY_ERROR:' in view:
        return None
    visible = [circle for row in re.findall(r'^VisibleFullCircle: (.*)',view,re.M)
               if (circle := numbers(row,7))]
    kind, diameter, outer, depth, through = callout
    if kind == 'thread':
        attached = [row['circle'] for target in _thread_targets(record,transform,visible) for row in target['targets']]
        attached += [circle for row in re.findall(r'^IndexedDimensionCircularEdge: \d+ \| (.*)',record,re.M)
                     if (circle := numbers(row,7))]
    else:
        attached = [circle for row in re.findall(r'^DimensionCircularOpening: \S+ \| \S+ \| (.*)',record,re.M)
                    if (circle := numbers(row,7))]
    matches = []
    for wizard in wizards:
        p = wizard['parameters']
        limit = max(p['holeDepth'],p['depth'],p['threadDepth'],p['counterboreDepth'],.0005)
        owned = [item['circle'] for item in openings if item['featureId'] == wizard['featureId']]
        owned.extend(rim for wall in bore_walls if wall['featureId'] == wizard['featureId'] for rim in wall['rims']
                     if abs(rim[6]-wall['cylinder'][6]) <= 1e-7)
        if kind == 'thread':
            if not _mm_equal(p['threadDiameter'],diameter):
                continue
        elif kind == 'counterbore':
            if not (_mm_equal(p['throughDiameter'],diameter) and _mm_equal(p['counterboreDiameter'],outer)):
                continue
        elif not _mm_equal(p['holeDiameter'],diameter):
            continue
        for seed in attached:
            if not any(same_circle(seed,circle) for circle in visible):
                continue
            seed_owned = any(same_circle(seed,circle) for circle in owned)
            if kind == 'thread':
                tap = p['tapDrillDiameter']
                if tap > 0 and not (_mm_equal(seed[6]*2,tap*1000) or _mm_equal(seed[6]*2,diameter)):
                    continue
                if tap <= 0 and seed[6]*2000 > diameter+.01:
                    continue
            elif not seed_owned or not _mm_equal(seed[6]*2,diameter):
                continue
            if not any(_align(center,seed,None if seed_owned else limit) for center in wizard['holeCenters']):
                continue
            points = _point_group(wizard,seed,visible,owned,transform,limit,bore_walls)
            if points is not None:
                matches.append(dict(wizard=wizard, visibleHoles=points, kind=kind, diameterMm=diameter,
                                    depthMm=depth, outerDiameterMm=outer, throughCallout=through))
                break
    if len(matches) != 1:
        return None
    result = matches[0]
    wizard = result['wizard']
    p = wizard['parameters']
    # Cosmetic thread length and the end condition of the cut are independent.
    depth_property = {'thread':'threadDepth','counterbore':'counterboreDepth','blind_diameter':'holeDepth'}[kind]
    if depth is not None and p[depth_property] > 0:
        result['depthMatchesModel'] = _mm_equal(p[depth_property],depth)
    if kind == 'counterbore':
        result['throughMatchesModel'] = wizard['endCondition'] in (1,9)
    if kind == 'blind_diameter' and wizard['endCondition'] == 0:
        result['cylinderDepths'] = _blind_cylinder_depths(wizard,result['visibleHoles'],openings)
    result['visibleHoleCount'] = len(result['visibleHoles'])
    result['result'] = ('matched_wizard_group' if result['visibleHoleCount'] == quantity else 'wizard_scope_unconfirmed')
    return result
