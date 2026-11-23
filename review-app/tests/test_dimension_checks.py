import unittest

from dimension_checks import linear_chain_evidence


def dimension(name, start, end, *, parentheses=False, reference_y=None):
    """Horizontal dimension with two witness lines and a dimension line."""
    raw = '\r\n'.join([
        f'DIM: {name} | SystemValue: {end-start:.6f} | Type: 2 | Reference: True',
        f'Parentheses: {parentheses}',
        f'DimPosition: {(start+end)/2},0.09,0, | Dangling: False',
        f'DimensionDisplayLine: 0,0,-1,-1,{start},0.04,0,{start},0.0905,0,',
        f'DimensionDisplayLine: 0,0,-1,-1,{end},0.04,0,{end},0.0905,0,',
        f'DimensionDisplayLine: 0,0,-1,-1,{start},0.09,0,{end},0.09,0,',
    ])
    if reference_y is None:
        return raw
    raw = raw.replace('Dangling: False', 'Dangling: False | AttachedCount: 2 |')
    return raw + (f'\r\nDimensionReferencePoint: {start},{reference_y},0,'
                  f'\r\nDimensionReferencePoint: {end},{reference_y},0,')


class DimensionChainTests(unittest.TestCase):
    def test_requires_matching_witnesses_and_excludes_reference_parentheses(self):
        view = ('View: Front Model: part\r\nViewMetadata: type=7 | scale=1 | configuration=Default\r\n'
                'ViewTransform: 1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,\r\n')
        segments = '\r\n'.join([dimension('D1', .01, .03, reference_y=.04),
                               dimension('D2', .03, .06, reference_y=.04)])
        total = dimension('D3', .01, .06, reference_y=.04)
        checks, closed = linear_chain_evidence(view + total + '\r\n' + segments)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]['total']['objectId'], 'D3')
        self.assertEqual([segment['objectId'] for segment in closed[0]['segments']], ['D1', 'D2'])
        self.assertTrue(all(check['result']=='verified_span' for check in checks))
        self.assertFalse(linear_chain_evidence(view + segments + '\r\n' + dimension('D3', .02, .07, reference_y=.04))[1])
        self.assertFalse(linear_chain_evidence(view + segments + '\r\n' + dimension('D3', .01, .06, reference_y=.05))[1])
        self.assertFalse(linear_chain_evidence(view + segments + '\r\n' + dimension('D3', .01, .06, parentheses=True, reference_y=.04))[1])
        self.assertFalse(linear_chain_evidence(view + segments + '\r\n' + view + total)[1])
