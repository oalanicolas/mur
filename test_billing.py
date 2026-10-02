import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from accounts import validate_subscriptions
from billing import summary, monthly_totals

TZ = ZoneInfo('America/Sao_Paulo')


def at(day):
    return dt.datetime.fromisoformat(day).replace(tzinfo=TZ).timestamp()


def period(identifier,amount,start='',end='',**changes):
    return {'id':identifier,'provider':'OpenAI','label':identifier,'monthlyUsd':amount,'quantity':1,
            'startDate':start,'endDate':end,**changes}


def report(rows,start='2026-09-24',end='2026-10-01'):
    return summary(rows,at(start),at(end),TZ,dt.datetime(2026,10,1,tzinfo=TZ))


class BillingTests(unittest.TestCase):
    def test_price_changes_on_tuesday_do_not_reprice_earlier_days(self):
        rows=[period('old',200,'2026-09-01','2026-09-29'),period('new',500,'2026-09-29')]
        result=report(rows)
        self.assertAlmostEqual(result['allocated'],200*5/30+500*2/30)
        self.assertAlmostEqual(report(rows,'2026-09-24','2026-09-29')['allocated'],200*5/30)
        self.assertEqual(result['monthly'],500)

    def test_extra_accounts_expire_and_payment_is_not_multiplied(self):
        row=period('extra',200,'2026-09-21','2026-09-28',provider='Anthropic',quantity=2,renews=False,
                   payments=[{'id':'paid','date':'2026-09-21','amountUsd':400}])
        self.assertAlmostEqual(report([row])['allocated'],400*4/30)
        self.assertEqual(report([row])['monthly'],0)
        result=report([row],'2026-09-21','2026-09-28')
        self.assertEqual(result['paidRecorded'],400)
        self.assertEqual(result['paymentsCount'],1)
        self.assertEqual(report([row],'2026-10-01','2026-10-08')['allocated'],0)

    def test_canceling_preserves_paid_coverage_and_does_not_invent_new_charges(self):
        row=period('grok',300,'2026-09-20','2026-10-20',provider='xAI',cancelDate='2026-09-29',
                   payments=[{'id':'grok-paid','date':'2026-09-20','amountUsd':300}])
        result=report([row])
        self.assertEqual(result['monthly'],0)
        self.assertEqual(result['allocated'],70)
        self.assertEqual(result['paidRecorded'],0)
        self.assertEqual(report([row],'2026-09-01','2026-10-01')['paidRecorded'],300)
        self.assertEqual(report([row],'2026-10-01','2026-11-01')['paidRecorded'],0)

    def test_cancel_with_unknown_coverage_is_partial_not_zero(self):
        result=report([period('grok',300,'2026-09-20',cancelDate='2026-09-29')])
        self.assertEqual(result['allocated'],50)
        self.assertEqual(result['subscriptionsMissingDates'],1)
        self.assertEqual(result['subscriptionsPending'],1)

    def test_unknown_start_is_not_assumed_to_be_forever(self):
        row=period('unknown',500)
        result=report([row])
        self.assertEqual(result['allocated'],0)
        self.assertEqual(result['subscriptionsMissingDates'],1)
        self.assertEqual(result['monthly'],500)

    def test_stopped_renewal_with_unknown_dates_is_not_future_commitment(self):
        result=report([period('extra',200,quantity=2,renews=False)])
        self.assertEqual(result['monthly'],0)
        self.assertEqual(result['subscriptionsPending'],2)

    def test_scheduled_start_and_cancel_respect_day_boundaries(self):
        rows=[period('later',500,'2026-10-02'),period('cancel-today',100,'2026-09-01',cancelDate='2026-10-01')]
        self.assertEqual(sum(monthly_totals(rows,'2026-09-30').values()),100)
        self.assertEqual(sum(monthly_totals(rows,'2026-10-01').values()),0)
        self.assertEqual(sum(monthly_totals(rows,'2026-10-02').values()),500)

    def test_same_account_accepts_adjacent_periods_rejects_overlaps(self):
        accounts=[{'id':'known','provider':'OpenAI'}]
        old=period('old',200,'2026-09-01','2026-09-29',accountId='known')
        new=period('new',500,'2026-09-29',accountId='known')
        self.assertEqual(len(validate_subscriptions([old,new],accounts)),2)
        with self.assertRaises(ValueError):validate_subscriptions([old,dict(new,startDate='2026-09-28')],accounts)

    def test_invalid_dates_and_duplicate_payments_are_rejected(self):
        base=period('base',100,'2026-09-01')
        for change in [{'startDate':'yesterday'},{'endDate':'2026-08-31'},{'cancelDate':'2026-08-31'},
                       {'renews':'false'},{'payments':[{'id':'p','date':'2026-02-30','amountUsd':100}]},
                       {'payments':[{'id':'p','date':'2026-09-01','amountUsd':-5}]}]:
            with self.assertRaises(ValueError):validate_subscriptions([dict(base,**change)],[])
        payment={'id':'same','date':'2026-09-01','amountUsd':100}
        with self.assertRaises(ValueError):validate_subscriptions([dict(base,payments=[payment,payment])],[])

    def test_calendar_days_remain_days_across_dst(self):
        zone=ZoneInfo('America/New_York')
        start=dt.datetime(2026,3,7,tzinfo=zone).timestamp()
        end=dt.datetime(2026,3,10,tzinfo=zone).timestamp()
        result=summary([period('dst',300,'2026-03-01')],start,end,zone)
        self.assertEqual(result['allocated'],30)


if __name__=='__main__':unittest.main()
