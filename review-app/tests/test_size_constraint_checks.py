import unittest

from size_constraint_checks import constraint_findings


def dim(name, start, end, *, reference=False, ordinate=0):
    return dict(id=name, kind='dimension', view='v1', dangling=False, box=[.1,.2,.5,.3],
                renderedText=[dict(text=f'{(end-start)*1000:.6g}')],
                constraintEvidence=dict(visibilityApi=1, shownAsReference=reference, typeCode=2,
                                        systemValue=end-start,
                                        verifiedLinearSpan=dict(modelDirection=(1,0,0),
                                                                modelPointsM=[(start,ordinate,0),(end,ordinate,0)])))


def page(number, dimensions, model_key='part-config'):
    view = dict(id=f'p{number}-view', kind='view', view='v1', name=f'View {number}', modelKey=model_key,
                metadata={'type':4}, transform=(1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0),
                dimensionContext=dict(cropped=False,modelBreak=False,breakCount=0,breakOuts=0))
    envelope = dict(id=f'p{number}-env', kind='modelEnvelope', view='v1', status='complete',
                    solidBodies=1, box=[.1,.2,.5,.6], extrema=[
                        dict(axis='x',modelPointM=(0,0,0)),dict(axis='x',modelPointM=(.1,0,0))])
    edge = dict(id=f'p{number}-edge',kind='visibleEdge',view='v1',shape='line',
                modelCoordinatesM=(0,0,0,.1,0,0))
    return dict(page=number,elements=[view,envelope,edge,*dimensions],extractionErrors=[],drawingUnits=[0])


class SizeConstraintTests(unittest.TestCase):
    def test_internal_span_alone_does_not_cover_either_outline_end(self):
        rows = list(constraint_findings({'pages':[page(1,[dim('internal',.02,.08)])]}, {6}, []))
        self.assertEqual([row['status'] for row in rows],['missing'])
        self.assertEqual(rows[0]['objectId'],'p1-env')

    def test_cross_page_chain_satisfies_envelope_only_with_a_shared_endpoint(self):
        first = page(1,[dim('D1',0,.04)])
        second = page(2,[dim('D2',.04,.1)])
        good = list(constraint_findings({'pages':[first,second]}, {6}, []))
        self.assertEqual([row['status'] for row in good],['pass'])
        offset = page(2,[dim('D2',.04,.1,ordinate=.01)])
        unconnected = list(constraint_findings({'pages':[first,offset]}, {6}, []))
        self.assertEqual([row['status'] for row in unconnected],['problem'])

    def test_cross_view_loop_excludes_reference_and_other_config(self):
        first = page(1,[dim('overall',0,.1)])
        segments = [dim('D1',0,.04),dim('D2',.04,.1)]
        found = list(constraint_findings({'pages':[first,page(2,segments)]}, {7}, []))
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['objectId'],'overall')
        self.assertEqual(found[0]['evidence']['dimensions'],[{'page':2,'id':'D1'},{'page':2,'id':'D2'}])
        for other in (page(2,[segments[0],dim('D2',.04,.1,reference=True)]),
                      page(2,[segments[0],dim('D2',.04,.1,ordinate=.01)]),
                      page(2,segments,model_key='another-config')):
            self.assertFalse(list(constraint_findings({'pages':[first,other]}, {7}, [])))
