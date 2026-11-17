import unittest

from calculator import Calculator

class CalculatorTests(unittest.TestCase):
    def test_background_calculation_preserves_reset_clock(self):
        start = 1794787200
        model = {'hourlyByThreeHours': [[100/24]*8 for _ in range(7)],
                 'dailyRates': [100]*7,
                 'account': {'capturedAt': start, 'remaining': 0,
                             'nextRefresh': start+12*3600,
                             'expiries': [start+10*86400]}}
        actual = Calculator().optimize(model,days=1)
        self.assertAlmostEqual(actual['demand'],100)
        self.assertAlmostEqual(actual['baseline'],50)
        self.assertAlmostEqual(actual['now']['served'],100)
        self.assertEqual(actual['now']['schedule'][0]['refresh'],start+7*86400)

    def test_native_options_change_planning_period(self):
        start=1794787200
        model={'hourlyByThreeHours': [[1]*8 for _ in range(7)],'dailyRates':[24]*7,
               'account':{'capturedAt':start,'remaining':100,'nextRefresh':start+7*86400,'expiries':[]}}
        calc=Calculator()
        short=calc.optimize(model,days=14,strength=.5)
        long=calc.optimize(model,days=28,strength=1)
        self.assertAlmostEqual(short['demand'],168)
        self.assertAlmostEqual(long['demand'],672)
        self.assertEqual(len(short['rows']),15)
        self.assertEqual(len(long['rows']),29)

    def test_one_point_work_return_uses_full_weekly_allowance(self):
        start=1794787200
        model={'hourlyByThreeHours': [[1/24]*8 for _ in range(7)],'dailyRates':[1]*7,
               'account':{'capturedAt':start,'remaining':50,'nextRefresh':start+7*86400,'expiries':[]}}
        result=Calculator().optimize(model,days=1)
        marginal=result['marginal']
        self.assertEqual(marginal['unit'],1)
        self.assertAlmostEqual(marginal['totalGain'],1)
        self.assertAlmostEqual(marginal['before']['total'],1)
        self.assertAlmostEqual(marginal['after']['total'],2)


if __name__=='__main__':unittest.main()
