"""SolidWorks drawing evidence in the coordinate system of the rendered PDF."""

import hashlib
import json
import math
import re
import tempfile
import time
from collections import Counter
from itertools import combinations
from pathlib import Path

from dimension_checks import LINEAR_HEADER, _confirmed_span, display_segments, project_model_point
from graphic_checks import graphic_inventory_read, page_graphics
from signature_checks import drawing_notes, inspect_signature_cells
from slot_checks import numbers, read_openings, same_circle, same_line
from template_checks import inspect_sheet_format
from title_field_checks import inspect_title_fields


def records(raw, key):
    return re.findall(r'^' + re.escape(key) + r': ([^\r\n]*)', raw, re.M)


def first(raw, key):
    return next(iter(records(raw, key)), '')


def vector(raw):
    return numbers(raw, len(raw.strip().rstrip(',').split(',')))


def view_metadata(value):
    fields = dict(part.split('=', 1) for part in value.split(' | ') if '=' in part)
    scale = numbers(fields.get('scale',''), 1)
    return dict(type=int(fields['type']) if fields.get('type','').isdigit() else None,
                scale=scale[0] if scale else None, configuration=fields.get('configuration'))


def _view_identity(header, raw):
    name, _, model = header.rstrip('\r').partition(' Model: ')
    path = model.strip().replace('\\', '/')
    metadata = view_metadata(first(raw, 'ViewMetadata'))
    identity = path.casefold()+'\n'+(metadata['configuration'] or '').casefold()
    key = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20] if path else None
    return name.strip(), path.rsplit('/', 1)[-1], key, metadata


def api_fields(value):
    return dict(part.split('=', 1) for part in value.split(' | ') if '=' in part)


def api_bool(value):
    return {'True':True, 'False':False}.get(value)


def api_int(value):
    return int(value) if value and value.lstrip('-').isdigit() else None


def _dimension_attachment_data(raw, count, selection_types):
    attachments = {i:dict(index=i, selectionType=int(selection_types[i])
                          if selection_types and i < len(selection_types) else None)
                   for i in range(count or 0)}
    for line in records(raw, 'DimensionAttachmentContext'):
        i, selection, component, configuration = line.split(' | ', 3)
        attachments.setdefault(int(i), dict(index=int(i))).update(
            selectionType=api_int(selection), component=component or None, componentConfiguration=configuration or None)
    for line in records(raw, 'AttachmentPersistentReference'):
        i, scope, reference = line.split(' | ', 2)
        attachments.setdefault(int(i), dict(index=int(i))).setdefault('persistentReferences', {})[scope] = reference
    for label, field, length in (
        ('IndexedDimensionVertex','modelVertexM',3), ('IndexedDimensionStraightEdge','modelLineEndpointsM',6),
        ('IndexedDimensionCircularEdge','modelCircle',7), ('IndexedDimensionSilhouette','silhouetteEndpointsApi',6),
        ('AttachedSketchPoint','sketchPointApi',3), ('AttachedSketchLine','sketchLineApi',6),
        ('AttachedSketchArc','sketchArcApi',4), ('AttachedSketchTransform','modelToSketch',16),
        ('AttachedCenterMark','centerMarkPointApi',3), ('AttachedCenterlineDisplayLine','centerlineDisplayLineApi',10),
    ):
        for line in records(raw, label):
            i, _, value = line.partition(' | ')
            point = numbers(value, length)
            if i.isdigit() and point:
                attachments.setdefault(int(i), dict(index=int(i))).setdefault(field, []).append(point)
    for i in records(raw, 'DimensionAttachmentNull'):
        attachments.setdefault(int(i), dict(index=int(i)))['isNull'] = True
    return list(attachments.values())


def _dimension_data(raw, fields, position, coords, transform):
    state = api_fields(first(raw, 'DimensionState'))
    placement = dict(part.split(': ',1) for part in position[1:] if ': ' in part)
    dtype = api_int(fields.get('Type'))
    value = numbers(fields.get('SystemValue',''), 1)
    pts = [point for row in records(raw,'DimensionReferencePoint') if (point := numbers(row,3))]
    count = api_int(placement.get('AttachedCount'))
    printed = ''.join(text.rpartition(' position=')[0] for text in records(raw,'RenderedDimText'))
    quantity = re.match(r'^\s*(\d+)\s*[-×xX*]', printed)
    result = dict(typeCode=dtype, systemValue=value[0] if value else None,
                  systemUnit='rad' if dtype in (3,16) else 'm' if dtype in range(1,13) or dtype in (14,15) else None,
                  displayedQuantity=int(quantity[1]) if quantity else None,
                  referenceApi=api_bool(fields.get('Reference')),
                  shownAsReference=api_bool(first(raw,'Parentheses')) is True or printed.lstrip().startswith(('(','（')),
                  drivenStateApi=api_int(state.get('driven')), visibilityApi=api_int(state.get('visibility')),
                  annotationPersistentId=state.get('annotationId') or None,
                  toleranceTypeApi=api_int(fields.get('ToleranceType')), toleranceValuesApi=vector(fields.get('Tolerance','')),
                  referencePointsApi=pts, referencePointCountApi=api_int(placement.get('ReferencePoints')),
                  attachedCountApi=count, attachments=_dimension_attachment_data(raw,count,vector(placement.get('AttachedTypes',''))),
                  verifiedLinearSpan=None)
    if transform and transform[12] > 0 and LINEAR_HEADER.match(raw):
        matched = _confirmed_span(raw, transform)
        if matched:
            axis, _, span = matched
            witnesses = [project_model_point(point,transform) for point in span['modelReferencePoints']]
            result['verifiedLinearSpan'] = dict(axis='x' if axis==0 else 'y', modelPointsM=span['modelReferencePoints'],
                                                modelDirection=[transform[3*i+axis] for i in range(3)],
                                                sheetPointsM=witnesses, pdfPointsPt=[coords.point(p) for p in witnesses])
    return result


def _view_centers(raw, coords):
    for row in records(raw, 'ViewCenter'):
        kind, index, visible, reference, name = row.split(' | ',4)
        prefix = kind+' | '+index+' | '
        lines = [p for line in records(raw,'ViewCenterDisplayLine') if line.startswith(prefix)
                 and (p := numbers(line[len(prefix):],10))]
        segments = [(p[4:6],p[7:9]) for p in lines]
        marks = [p[:2] for line in records(raw,'ViewCenterMarkPoint') if line.startswith(index+' | ')
                 and (p := numbers(line.partition(' | ')[2],3))] if kind=='centerMark' else []
        refs = [line[len(prefix):].partition(' | ')[2] for line in records(raw,'ViewCenterAttachment') if line.startswith(prefix)]
        bounds = extent(marks + [p for pair in segments for p in pair])
        yield kind, bounds, marks[0] if len(marks)==1 else None, dict(
            name=name, visibilityApi=api_int(visible), annotationPersistentId=reference or None,
            attachedDrawingReferences=[ref for ref in refs if ref], sheetMarkPointsM=marks,
            pdfMarkPointsPt=[coords.point(p) for p in marks],
            pdfSegmentsPt=[[coords.point(a),coords.point(b)] for a,b in segments])


def extent(points):
    if not points:
        return None
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _circle_bounds(circle, transform):
    x, y = project_model_point(circle[:3], transform)
    axis = circle[3:6]
    radius = circle[6] * abs(transform[12])
    dx, dy = [radius * math.sqrt(max(0, 1-sum(axis[i]*transform[3*i+j] for i in range(3))**2))
              for j in range(2)]
    return (x-dx,y-dy,x+dx,y+dy), (x,y)


def _feature_projection(line, circles, lines):
    values = []
    edge_matches = []
    if line.startswith('ModelCircularOpening:'):
        values = [numbers(line.split(' | ', 2)[1], 7)] if ' | ' in line else []
    elif line.startswith('ModelStraightSlotOpening:'):
        slot = next(read_openings(line), None)
        if slot is None:
            return None, []
        _, _, values, edges = slot
        edge_matches = [next((entry for entry in lines if same_line(edge, entry[0])), None) for edge in edges]
        if any(entry is None for entry in edge_matches):
            return None, []
    matched = [next((entry for entry in circles if same_circle(value, entry[0])), None)
               for value in values if value]
    if not matched or len(matched) != len(values) or any(entry is None for entry in matched):
        return None, []
    matched += edge_matches
    bounds = extent([point for _, box, _ in matched for point in (box[:2],box[2:])])
    return bounds, [ident for _, _, ident in matched]


class PdfCoordinates:
    def __init__(self, pdf_page, size, border):
        self.width, self.height = pdf_page.rect.width, pdf_page.rect.height
        self.size = size
        self.calibration = 'unavailable'
        self.transform = None
        if not size or pdf_page.rotation:
            return
        w, h = size
        sx, sy = self.width/w, self.height/h
        if abs(sx/sy - 1) > .01:
            return
        self.calibration = 'sheet-size'
        self.transform = (sx, sy, 0, self.height)
        if not border:
            return
        left, bottom, right, top = border
        # The printer can introduce an offset. Match all four long border lines
        # near their expected positions before accepting a measured transform.
        axes = ([], [])
        for path in pdf_page.get_drawings():
            for item in path['items']:
                if item[0] != 'l':
                    continue
                a, b = item[1:]
                if abs(a.x-b.x) < .2 and abs(a.y-b.y) > .75*self.height:
                    axes[0].append(a.x)
                if abs(a.y-b.y) < .2 and abs(a.x-b.x) > .75*self.width:
                    axes[1].append(a.y)
        expected = (left*sx, right*sx, self.height-top*sy, self.height-bottom*sy)
        measured = [min(axes[i//2], key=lambda x: abs(x-target), default=math.inf)
                    for i, target in enumerate(expected)]
        if all(abs(a-b) <= 3 for a,b in zip(expected, measured)):
            x0,x1,y0,y1 = measured
            sx, sy = (x1-x0)/(right-left), (y1-y0)/(top-bottom)
            self.transform = (sx, sy, x0-left*sx, y0+top*sy)
            self.calibration = 'pdf-frame'

    def point(self, p):
        if p is None or self.transform is None:
            return None
        sx, sy, ox, oy = self.transform
        return [round(ox+p[0]*sx, 4), round(oy-p[1]*sy, 4)]

    def location(self, bounds=None, anchor=None):
        box = norm = None
        point = self.point(anchor)
        if bounds is not None and self.transform is not None:
            x0,y0,x1,y1 = bounds
            box = self.point((x0,y1)) + self.point((x1,y0))
            clipped = [max(0, box[0]/self.width), max(0, box[1]/self.height),
                       min(1, box[2]/self.width), min(1, box[3]/self.height)]
            if clipped[0] < clipped[2] and clipped[1] < clipped[3]:
                norm = clipped
        status = 'box' if norm else 'geometry' if box else 'anchor_only' if point else 'unlocated'
        return dict(positionStatus=status, sheetBoundsM=bounds, sheetAnchorM=anchor,
                    pdfBoxPt=box, pdfAnchorPt=point, box=norm)


def _opening_instances(raw, instance, wizard_holes):
    def axial_depth(points, axis):
        offsets = [sum(a*b for a, b in zip(point, axis)) for point in points]
        return max(offsets) - min(offsets) if len(offsets) > 1 and max(offsets)-min(offsets) > 1e-7 else None

    rims, slots, groups = {}, {}, {}
    openings = {}
    for row in records(raw, 'SpatialModelCircularOpening'):
        edge, wall, *_ = row.split(' | ')
        openings.setdefault(wall, set()).add(edge)
    for row in records(raw, 'SpatialModelCircularWallRim'):
        wall, values = row.split(' | ', 1)
        if circle := numbers(values, 7):
            rims.setdefault(wall, []).append(circle[:3])
    wizard_by_id = {hole['persistentId']: hole for hole in wizard_holes}
    holes = []
    for row in records(raw, 'SpatialModelBoreWall'):
        wall, values, *properties = row.split(' | ')
        if wall not in rims or not (cylinder := numbers(values, 7)):
            continue
        owner = api_fields(' | '.join(properties)).get('owner')
        wizard = wizard_by_id.get(owner)
        diameter = cylinder[6] * 2
        depth = axial_depth(rims[wall], cylinder[3:6])
        through = len(openings.get(wall, ())) >= 2 and depth is not None
        hole = dict(id=f'{instance}:{wall}', wallPersistentId=wall, featurePersistentId=owner,
                    type='wizard' if wizard else 'cylindrical', axis=cylinder[3:6],
                    rimCentersPartM=rims[wall], diameterM=diameter, cylindricalDepthM=depth,
                    rimEdgePersistentIds=sorted(openings.get(wall, ())),
                    throughConfirmed=through, wizardFeatureId=wizard['id'] if wizard else None)
        if wizard:
            hole['wizardPointIndex'] = next((i for i, point in enumerate(wizard['points'])
                                             if point['partPointM'] and
                                             any(math.dist(point['partPointM'], rim) <= 1e-7 for rim in rims[wall])), None)
        holes.append(hole)
        key = (hole['type'], wizard['typeCode'] if wizard else None, wizard['fastenerSize'] if wizard else None,
               round(diameter, 7), round(depth, 7) if depth is not None else None, through,
               tuple(wizard['parametersM'].items()) if wizard else ())
        groups.setdefault(key, []).append(hole['id'])

    for walls, size, arcs, _ in read_openings(re.sub(r'^Spatial(?=ModelStraightSlotOpening:)', '', raw, flags=re.M)):
        slot = slots.setdefault(walls, dict(id=f'{instance}:{walls[0]}:{walls[1]}', wallPersistentIds=walls,
                                            widthM=size[0], lengthM=size[1], entries=[]))
        slot['entries'].append(dict(arcCentersPartM=[arc[:3] for arc in arcs], normal=arcs[0][3:6]))
    for slot in slots.values():
        entries = slot['entries']
        slot['depthM'] = axial_depth([entry['arcCentersPartM'][0] for entry in entries], entries[0]['normal'])
    return holes, [
        dict(type=kind, wizardTypeCode=code, fastenerSize=size, diameterM=diameter, cylindricalDepthM=depth,
             throughConfirmed=through, parametersM=dict(parameters), count=len(ids), holeIds=ids)
        for (kind, code, size, diameter, depth, through, parameters), ids in groups.items()
    ], list(slots.values())


def _spatial_model(raw):
    header = first(raw, 'SpatialRoot').split(' | ', 2)
    if len(header) != 3:
        return None
    path, configuration, kind = header
    components, faces, distances = [], [], []
    errors = records(raw, 'SpatialModelReadError') + records(raw, 'SpatialWizardHoleReadError')
    parameter_names = (
        'holeDiameter', 'diameter', 'thruHoleDiameter', 'tapDrillDiameter',
        'threadDiameter', 'counterBoreDiameter', 'counterSinkDiameter',
        'holeDepth', 'depth', 'threadDepth', 'counterBoreDepth',
    )
    for chunk in re.split(r'^SpatialPart: ', raw, flags=re.M)[1:]:
        header, _, body = chunk.partition('\n')
        instance, part_path, part_config = header.rstrip('\r').split(' | ', 2)
        box = numbers(first(body, 'SpatialBox'), 6)
        transform = numbers(first(body, 'SpatialPartTransform'), 16)
        face_ids = []
        for row in records(body, 'SpatialFace'):
            persistent_id, normal, point = row.split(' | ', 2)
            normal, point = numbers(normal, 3), numbers(point, 3)
            if normal and point and (length := math.hypot(*normal)):
                face_id = f'{instance}:{persistent_id}'
                faces.append(dict(id=face_id, componentId=instance, persistentId=persistent_id,
                                  normal=[v / length for v in normal], pointM=point))
                face_ids.append(face_id)
        parameters = {key: numbers(value, len(parameter_names))
                      for key, value in (row.split(' | ', 1) for row in records(body, 'SpatialModelWizardHoleParameters'))}
        points = {}
        for row in records(body, 'SpatialModelWizardHolePoint'):
            key, sketch, model = row.split(' | ', 2)
            part_point = numbers(model, 3)
            root_point = part_point if kind == 'part' else None
            if part_point and transform:
                root_point = [
                    transform[9+axis] + transform[12] * sum(part_point[i]*transform[3*i+axis] for i in range(3))
                    for axis in range(3)
                ]
            points.setdefault(key, []).append(dict(sketchPointM=numbers(sketch, 3),
                                                   partPointM=part_point, rootPointM=root_point))
        holes = []
        for row in records(body, 'SpatialModelWizardHole'):
            key, label, *properties = row.split(' | ')
            fields = api_fields(' | '.join(properties))
            holes.append(dict(id=f'{instance}:{key}', persistentId=key, name=label,
                              typeCode=api_int(fields.get('type')), fastenerSize=fields.get('size'),
                              endConditionCode=api_int(fields.get('end')), sketchPointCount=api_int(fields.get('points')),
                              parametersM=dict(zip(parameter_names, parameters.get(key) or [])),
                              points=points.get(key, [])))
        sheet_metal = []
        for row in records(body, 'SpatialSheetMetal'):
            key, label, values = row.split(' | ', 2)
            if data := numbers(values, 3):
                sheet_metal.append(dict(persistentId=key, name=label, thicknessM=data[0],
                                        defaultBendRadiusM=data[1], kFactor=data[2]))
        bends = []
        for row in records(body, 'SpatialBend'):
            key, label, face, values = row.split(' | ', 3)
            if data := numbers(values, 2):
                bends.append(dict(id=f'{instance}:{face}', featurePersistentId=key, facePersistentId=face,
                                  name=label, angleRad=data[0], angleDeg=math.degrees(data[0]),
                                  innerRadiusM=data[1], source='inner-cylinder-boundary-arc'))
        circular_holes, hole_groups, slots = _opening_instances(body, instance, holes)
        slot_groups = {}
        for slot in slots:
            size = tuple(round(slot[field], 7) if slot[field] is not None else None
                         for field in ('widthM', 'lengthM', 'depthM'))
            slot_groups.setdefault(size, []).append(slot['id'])
        components.append(dict(id=instance, path=part_path, configuration=part_config,
                               partToRootTransform=transform,
                               boundingBoxM=dict(min=box[:3], max=box[3:]) if box else None,
                               planarFaceIds=face_ids, wizardHoles=holes, circularHoles=circular_holes,
                               holeGroups=hole_groups, straightSlots=slots, sheetMetal=sheet_metal, bends=bends,
                               materialFeatureLabels=records(body, 'SpatialMaterialLabel'),
                               slotGroups=[dict(widthM=w, lengthM=l, depthM=d, count=len(ids), slotIds=ids)
                                           for (w, l, d), ids in slot_groups.items()]))
    distances_truncated = False
    for a, b in combinations(faces, 2):
        if abs(abs(sum(x*y for x, y in zip(a['normal'], b['normal']))) - 1) <= 1e-10:
            if len(distances) == 512:
                distances_truncated = True
                break
            gap = abs(sum((y-x)*n for x, y, n in zip(a['pointM'], b['pointM'], a['normal'])))
            distances.append(dict(faceIds=[a['id'], b['id']], distanceM=gap))
    boxes = [component['boundingBoxM'] for component in components]
    complete_boxes = boxes and all(boxes) and not records(raw, 'SpatialModelReadError')
    root_box = (dict(min=[min(box['min'][i] for box in boxes) for i in range(3)],
                     max=[max(box['max'][i] for box in boxes) for i in range(3)])
                if complete_boxes else None)
    return dict(path=path, configuration=configuration, kind=kind, coordinateFrame='referenced-model-xyz',
                geometryScope='component-part-solid-bodies' if kind == 'assembly' else 'part-solid-bodies',
                boundingBoxM=root_box, components=components, planarFaces=faces,
                parallelPlaneDistances=distances, parallelPlaneDistancesTruncated=distances_truncated,
                extractionErrors=errors)


def _sheet(page, raw, name, index, regions, models):
    size = numbers(first(raw, 'SheetSize_m').split(' Template:', 1)[0], 2)
    if size and min(size) <= 0:
        size = None
    visible_format = first(raw, 'SheetFormatVisible') != 'False'
    border, title = regions(raw, *size) if size and visible_format else (None, None)
    coords = PdfCoordinates(page, size, border)
    elements = []
    counts = Counter()
    graphics = page_graphics(raw)
    graphics_read = graphic_inventory_read(raw)

    def add(kind, bounds=None, anchor=None, *, view=None, **data):
        counts[kind] += 1
        ident = f'p{index}-{kind}-{counts[kind]}'
        elements.append(dict(id=ident, kind=kind, view=view, **coords.location(bounds, anchor), **data))
        return ident

    if border:
        add('frame', border)
    if title:
        add('titleBlock', title)
        format_checks = inspect_sheet_format(raw, title)
        a1 = any(check['field']=='template_code' and check['result']=='passed' for check in format_checks)
        for field in inspect_title_fields(raw, title, a1, graphics, graphics_read):
            values = [dict(text=value['text'], propertyLinks=value['binding'], noteName=value['name'])
                      for value in field['values']]
            add('titleField', field['cellBounds'], field=field['field'], required=field['required'],
                inspection=field['result'], values=values, sourceProperties=field['sourceProperties'],
                sourceView=field['sourceView'])
        for cell in inspect_signature_cells(raw, title):
            if cell.get('cellBounds'):
                add('signatureCell', cell['cellBounds'], role=cell['role'], field=cell['field'],
                    inspection=cell['result'], texts=[entry['text'] for entry in cell.get('entries',[])])
    for graphic in graphics:
        bounds, origin = graphic.pop('bounds'), graphic.pop('origin', None)
        add(graphic.pop('kind'), bounds, origin[:2] if origin else None, **graphic)

    parts = re.split(r'^View: ', raw, flags=re.M)
    view_ids = {}
    inventories = {}
    for n,part in enumerate(parts[1:], 1):
        header, _, body = part.partition('\n')
        name, _, model_key, _ = _view_identity(header, body)
        view_ids[name] = f'v{n}'
        if model_key and first(body, 'PartViewInventory') == 'True':
            features = inventories.setdefault(model_key, {})
            for line in body.splitlines():
                if line.startswith(('ModelCircularOpening:', 'ModelStraightSlotOpening:')):
                    features.setdefault(line, f'v{n}')
    for graphic in elements:
        if graphic['kind'] in ('picture','block','ole'):
            graphic['view'] = view_ids.get(graphic['scope'])
    for n, part in enumerate(parts):
        view = None
        transform = None
        model_key = None
        if n:
            header, _, part = part.partition('\n')
            view_name, model, model_key, metadata = _view_identity(header, part)
            view = f'v{n}'
            transform = numbers(first(part, 'ViewTransform'), 16)
            context = api_fields(first(part, 'ViewDimensionContext'))
            envelope_status, _, body_count = first(part, 'ViewEnvelopeStatus').partition(' | solidBodies=')
            if model_key and (model_key not in models or models[model_key]['extractionErrors']):
                spatial = _spatial_model(part)
                if spatial and (model_key not in models or
                                len(spatial['extractionErrors']) < len(models[model_key]['extractionErrors'])):
                    models[model_key] = spatial
            add('view', numbers(first(part, 'ViewOutline'), 4), view=view,
                name=view_name, model=model, modelKey=model_key,
                metadata=metadata, transform=transform,
                detailBaseView=first(part, 'DetailBaseView'),
                dimensionContext=dict(baseView=context.get('baseView') or None, cropped=api_bool(context.get('cropped')),
                                      breakCount=api_int(context.get('breakLines')), breakOuts=api_int(context.get('breakOuts')),
                                      modelBreak=api_bool(context.get('modelBreak')), modelEnvelopeStatus=envelope_status or None,
                                      centerInventoryRead='ViewCenterInventoryEnd' in part))
            extrema = []
            if transform:
                for row in records(part, 'ViewEnvelopeExtreme'):
                    axis, sign, value = row.split(' | ',2)
                    point = numbers(value,3)
                    if axis in ('0','1') and sign in ('-1','1') and point:
                        extrema.append(dict(axis='x' if axis=='0' else 'y', side='min' if sign=='-1' else 'max',
                                            modelPointM=point, projectedSheetPointM=project_model_point(point,transform)))
            if extrema:
                # Envelope points describe the complete model. Only use them to
                # locate a paper region for an uncropped full single-body view.
                paper = (metadata['type'] in (4,5,6,7,8) and body_count=='1' and context.get('cropped')=='False'
                         and context.get('breakLines')=='0' and context.get('breakOuts')=='0'
                         and context.get('modelBreak')=='False' and envelope_status=='complete')
                bounds = extent([p['projectedSheetPointM'] for p in extrema]) if paper else None
                spans = {}
                if envelope_status == 'complete':
                    for axis,coordinate in (('x',0),('y',1)):
                        values = [p['projectedSheetPointM'][coordinate] for p in extrema if p['axis']==axis]
                        if len(values)==2 and transform[12]>0:
                            spans[axis] = (max(values)-min(values))/transform[12]
                add('modelEnvelope', bounds, view=view, extrema=extrema, status=envelope_status,
                    scope='referenced-part-all-solid-bodies', solidBodies=api_int(body_count), spansM=spans,
                    boundsBasis='model-extrema-projection' if paper else None)
            for kind,bounds,anchor,details in _view_centers(part,coords):
                add(kind,bounds,anchor,view=view,**details)
        note_raw = 'SheetFormatVisible: '+first(raw, 'SheetFormatVisible')+'\n'+part
        for note in drawing_notes(note_raw):
            add('note', note.pop('bounds'), view=view, **note)
        for table in re.split(r'^DrawingTable: ', part, flags=re.M)[1:]:
            header = table.splitlines()[0]
            p = numbers(header.partition(' | position=')[2], 3)
            cells = [dict(row=int(r), column=int(c), text=t) for r,c,t in
                     re.findall(r'^DrawingTableCell: (\d+),(\d+) \| (.*)', table, re.M)]
            add('table', anchor=p[:2] if p else None, view=view, metadata=header, cells=cells)
        for dim in re.split(r'^DIM: ', part, flags=re.M)[1:]:
            name, *fields = dim.splitlines()[0].split(' | ')
            position = first(dim, 'DimPosition').split(' | ')
            p = numbers(position[0], 3)
            fragments = []
            text_points = []
            for text in records(dim, 'RenderedDimText'):
                label, _, xyz = text.rpartition(' position=')
                point = numbers(xyz, 3)
                fragments.append(dict(text=label or text, pdfAnchorPt=coords.point(point)))
                if point:
                    text_points.append(point[:2])
            lines = list(display_segments(dim))
            arrow_points = [values[:2] for row in records(dim, 'DimensionArrowHead') if (values := numbers(row, 12))]
            leaders = []
            for row in records(dim, 'DimensionLeader'):
                values = vector(row)
                if values and len(values) % 3 == 0:
                    leaders.append([values[i:i+2] for i in range(0,len(values),3)])
            points = [point for line in lines for point in line] + text_points + arrow_points
            points.extend(point for leader in leaders for point in leader)
            if p:
                points.append(p[:2])
            details = [line for line in dim.splitlines()[1:] if line.startswith(
                ('DimensionLeader:', 'DimensionArrowHead:', 'DimensionReference', 'DimensionAttachment',
                 'DimensionCircular', 'DimensionStraight', 'DimensionThread', 'Attached'))]
            properties = dict(field.split(': ', 1) for field in fields if ': ' in field)
            add('dimension', extent(points), p[:2] if p else None, view=view,
                name=name, properties=properties, constraintEvidence=_dimension_data(dim,properties,position,coords,transform),
                textParts=first(dim, 'DimTextParts').split(' | '), renderedText=fragments,
                parentheses=first(dim, 'Parentheses') == 'True',
                dangling='Dangling: True' in position if len(position) > 1 else None,
                pdfSegmentsPt=[[coords.point(a), coords.point(b)] for a,b in lines],
                pdfArrowTipsPt=[coords.point(point) for point in arrow_points],
                pdfLeaderPointsPt=[[coords.point(point) for point in leader] for leader in leaders],
                boundsBasis='display-lines-and-anchor', associationRecords=details)
        circles = []
        edges = []
        if transform:
            for row in records(part, 'VisibleStraightEdge'):
                if values := numbers(row, 6):
                    points = [project_model_point(values[:3], transform), project_model_point(values[3:], transform)]
                    bounds = extent(points)
                    ident = add('visibleEdge', bounds, view=view, shape='line',
                                pdfEndpointsPt=[coords.point(p) for p in points], modelCoordinatesM=values)
                    edges.append((values,bounds,ident))
            full_circles = Counter(records(part, 'VisibleFullCircle'))
            for row in records(part, 'VisibleCircle') or list(full_circles.elements()):
                if values := numbers(row, 7):
                    full = full_circles[row] > 0
                    full_circles[row] -= full
                    bounds, center = _circle_bounds(values, transform)
                    ident = add('visibleEdge', bounds, center, view=view, shape='circle' if full else 'arc',
                                boundsBasis='projected-circle' if full else 'projected-supporting-circle',
                                modelCoordinatesM=values)
                    circles.append((values,bounds,ident))
        local_features = set()
        for line in part.splitlines():
            if line.startswith(('ModelFeature:', 'ModelCircularOpening:', 'ModelBoreWall:',
                                'ModelStraightSlotOpening:', 'ModelWizardHole:', 'ModelWizardHoleParameters:',
                                'ModelWizardHolePoint:', 'ModelPatternSeed:')):
                local_features.add(line)
                bounds, refs = _feature_projection(line, circles, edges)
                anchor = None
                basis = 'matched-visible-geometry' if refs else None
                if transform and line.startswith('ModelWizardHolePoint:'):
                    model_point = numbers(line.rsplit(' | ', 1)[-1], 3)
                    if model_point:
                        anchor = project_model_point(model_point, transform)
                        basis = 'projected-hole-sketch-point'
                add('modelFeature', bounds, anchor, view=view, apiRecord=line,
                    visibleElementIds=refs, boundsBasis=basis)
        if first(part, 'PartViewInventory') == 'True':
            for line, source in inventories.get(model_key, {}).items():
                if line in local_features:
                    continue
                bounds, refs = _feature_projection(line,circles,edges)
                if refs:
                    add('modelFeature', bounds, view=view, apiRecord=line, visibleElementIds=refs,
                        sourceView=source, boundsBasis='matched-visible-geometry')
    return dict(page=index, sheet=name, pdfSizePt=[coords.width,coords.height], sheetSizeM=size,
                calibration=coords.calibration, sheetToPdfTransform=coords.transform,
                drawingUnits=vector(first(raw, 'DrawingUnits')), sheetProperties=vector(first(raw, 'SheetProperties')),
                graphicInventoryRead=graphics_read,
                titleProperties=records(raw, 'TitleProperty'), elementCounts=dict(counts), elements=elements,
                extractionErrors=[line for line in raw.splitlines() if re.match(r'^[\w_]*[Ee][Rr][Rr][Oo][Rr]:', line)])


def build_evidence(pdf, pages, filename, regions):
    aligned = len(pdf) == len(pages)
    models = {}
    return dict(schemaVersion=3, source='SolidWorks API', drawing=filename, models=models,
                coordinates=dict(origin='top-left', x='right', y='down', pdfUnit='pt', boxUnit='normalized',
                                 boxOrder=['left','top','right','bottom']),
                pageAlignment='sheet-order' if aligned else 'unavailable', apiSheetCount=len(pages),
                pages=[_sheet(page, pages[i-1].get('raw','') if aligned else '',
                              pages[i-1].get('name') if aligned else None, i, regions, models)
                       for i,page in enumerate(pdf, 1)])


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        # Windows can briefly deny a rename while another process holds the file.
        for attempt in range(12):
            try:
                temporary.replace(path)
                break
            except OSError as error:
                if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 11:
                    raise
                time.sleep(min(.025 * 2**attempt, .25))
    finally:
        temporary.unlink(missing_ok=True)
