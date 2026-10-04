import copy
import unittest
import engine as e

def snap(now=None, bid=108.0):
    now = e.START+120 if now is None else now
    end=(int(now)//3600)*3600
    rows=[]
    for i in range(80):
        c=100+i*.1
        rows.append([end-(80-i)*3600,c-.08,c+.01,c-.09,c,c,100,100])
    return {'id':e.ID,'observed_unix':now,'observed_at':e.iso(now),'system_status':'online',
            'metadata':{'lot_decimals':8,'ordermin':'0.06','costmin':'0.45','status':'online'},
            'asks':[[bid+.01,10000]],'bids':[[bid,10000]],'closed_1h':rows}

class TrialTests(unittest.TestCase):
    def test_signal_known_before_fill(self):
        s=e.evaluate(e.fresh_state(),snap())
        self.assertEqual(len(s['fills']),1)
        self.assertLessEqual(s['latest_signal_inputs']['bar_end'],s['fills'][0]['unix'])
    def test_start_cash_only(self):
        s=e.evaluate(e.fresh_state(),snap(e.START-60))
        self.assertEqual(s['cash_eur'],500)
        self.assertFalse(s['fills'])
    def test_duplicate_snapshot_not_duplicate_fill(self):
        x=snap();s=e.evaluate(e.fresh_state(),x)
        self.assertEqual(s,e.evaluate(s,x))
    def test_repeat_bar_not_repeat_buy(self):
        s=e.evaluate(e.fresh_state(),snap())
        s=e.evaluate(s,snap(e.START+420))
        self.assertEqual(len(s['fills']),1)
    def test_costs_and_risk(self):
        s=e.evaluate(e.fresh_state(),snap());p=s['position']
        self.assertLessEqual(p['initial_cost'],400)
        self.assertLessEqual(p['planned_loss_eur'],10.000001)
        self.assertLess(s['equity_eur'],500)
        self.assertAlmostEqual(s['cash_eur']+p['initial_cost'],500)
    def test_stop_at_later_actual_quote_not_ideal_trigger(self):
        s=e.evaluate(e.fresh_state(),snap())
        stop=s['position']['stop']
        s=e.evaluate(s,snap(e.START+420,90))
        self.assertIsNone(s['position'])
        self.assertLess(s['fills'][-1]['model_price_eur'],stop)
        self.assertEqual(s['fills'][-1]['unix'],e.START+420)
    def test_partial_and_trailing(self):
        s=e.evaluate(e.fresh_state(),snap());q=s['position']['qty']
        s=e.evaluate(s,snap(e.START+420,116))
        self.assertTrue(s['position']['partial'])
        self.assertAlmostEqual(s['position']['qty'],q/2,7)
        s=e.evaluate(s,snap(e.START+720,110))
        self.assertIsNone(s['position'])
    def test_fixed_end_closes(self):
        s=e.evaluate(e.fresh_state(),snap())
        s=e.evaluate(s,snap(e.END+120,115))
        self.assertTrue(s['final']);self.assertIsNone(s['position'])
        self.assertEqual(s['final_delay_seconds'],120)
    def test_protocol_mismatch_is_not_reset(self):
        s=e.fresh_state();s['protocol_hash']='wrong'
        with self.assertRaises(ValueError): e.evaluate(s,snap())
    def test_depth_failure(self):
        with self.assertRaises(ValueError): e.book_price([[100,1]],2)
    def test_halted_prevents_new_entries(self):
        s=e.fresh_state();s['cash_eur']=449;s['equity_eur']=449
        s=e.evaluate(s,snap())
        self.assertTrue(s['halted']);self.assertFalse(s['fills'])
    def test_final_is_immutable(self):
        s=e.evaluate(e.fresh_state(),snap())
        s=e.evaluate(s,snap(e.END+120,115))
        self.assertEqual(s,e.evaluate(s,snap(e.END+420,120)))
    def test_gaps_are_disclosed(self):
        s=e.evaluate(e.fresh_state(),snap())
        s=e.evaluate(s,snap(e.START+1500))
        self.assertEqual(len(s['gaps']),1)
    def test_no_missed_signal_replay(self):
        s=e.evaluate(e.fresh_state(),snap(e.START+1800))
        self.assertFalse(s['fills'])

if __name__=='__main__': unittest.main(verbosity=2)
