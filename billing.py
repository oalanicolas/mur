import datetime as dt
import math
import re

PROVIDERS = ('OpenAI','Anthropic','xAI')


def date_value(value, label):
    if value in ('', None):
        return ''
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('Informe uma data válida para '+label+'.')
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError('Informe uma data válida para '+label+'.') from None


def validate_period(row, payment_ids):
    renews = row.get('renews',True)
    if not isinstance(renews,bool):raise ValueError('Informe se a assinatura ainda renova.')
    start = date_value(row.get('startDate'),'início')
    end = date_value(row.get('endDate'),'fim da vigência')
    cancel = date_value(row.get('cancelDate'),'cancelamento')
    if start and end and end <= start:
        raise ValueError('O fim da vigência deve ser posterior ao início.')
    if start and cancel and cancel < start:
        raise ValueError('O cancelamento não pode ocorrer antes do início.')
    payments = row.get('payments',[])
    if not isinstance(payments,list) or len(payments)>500:
        raise ValueError('Registre até 500 pagamentos por período de assinatura.')
    confirmed = []
    for payment in payments:
        if not isinstance(payment,dict):raise ValueError('Pagamento inválido.')
        identifier = payment.get('id')
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',identifier) or identifier in payment_ids:
            raise ValueError('Pagamento com identificação inválida ou repetida.')
        paid_on = date_value(payment.get('date'),'pagamento')
        amount = payment.get('amountUsd')
        if not paid_on or isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or not 0<=amount<=10000000:
            raise ValueError('Informe a data e o valor total do pagamento.')
        payment_ids.add(identifier)
        confirmed.append({'id':identifier,'date':paid_on,'amountUsd':amount})
    return {'startDate':start,'endDate':end,'cancelDate':cancel,'renews':renews,'payments':confirmed}


def monthly_totals(rows, today=None):
    today = today or dt.date.today().isoformat()
    result = dict.fromkeys(PROVIDERS,0)
    for row in rows:
        if row.get('renews',True) and (not row.get('startDate') or row['startDate']<=today) and not any(row.get(key) and row[key]<=today for key in ('endDate','cancelDate')):
            result[row['provider']] += (row['monthlyUsd'] or 0)*row.get('quantity',1)
    return {provider:round(value,2) for provider,value in result.items()}


def summary(rows, start, end, timezone, now=None):
    start = dt.datetime.fromtimestamp(start,timezone).replace(tzinfo=None)
    end = dt.datetime.fromtimestamp(end,timezone).replace(tzinfo=None)
    now = now or dt.datetime.now(timezone)
    today = now.date().isoformat()
    allocated, paid, payments_count, pending, known, missing_dates = 0,0,0,0,0,0
    monthly_pending = 0
    breakdown = []
    for row in rows:
        quantity = row.get('quantity',1)
        first = dt.datetime.fromisoformat(row['startDate']) if row.get('startDate') else None
        last = dt.datetime.fromisoformat(row['endDate']) if row.get('endDate') else None
        canceled = dt.datetime.fromisoformat(row['cancelDate']) if row.get('cancelDate') else None
        could_overlap = (first is None or first<end) and (last is None or last>start)
        unknown_end = not row.get('renews',True) and canceled is None and last is None
        uncertain_dates = could_overlap and (first is None or unknown_end or (canceled is not None and last is None and canceled<end))
        if uncertain_dates:missing_dates += quantity
        unresolved = uncertain_dates or (could_overlap and row['monthlyUsd'] is None)
        if unresolved:pending += quantity
        cutoff = last or canceled or end
        days = max(0,(min(end,cutoff)-max(start,first)).total_seconds()/86400) if first and not unknown_end else 0
        amount = days*row['monthlyUsd']*quantity/30 if row['monthlyUsd'] is not None else 0
        allocated += amount
        if days and row['monthlyUsd'] is not None:known += quantity
        if could_overlap:
            breakdown.append({'id':row['id'],'label':row['label'],'provider':row['provider'],'days':days,'allocated':amount,'pending':unresolved})
        if row.get('renews',True) and not any(row.get(key) and row[key]<=today for key in ('endDate','cancelDate')) and (not row.get('startDate') or row['startDate']<=today):
            if row['monthlyUsd'] is None:monthly_pending += quantity
        for payment in row.get('payments',[]):
            day = dt.date.fromisoformat(payment['date'])
            if start.date()<=day and (day<end.date() or (day==end.date() and end.time()!=dt.time())):
                paid += payment['amountUsd']
                payments_count += 1
    return {'monthly':sum(monthly_totals(rows,today).values()),'allocated':round(allocated,10),
            'paidRecorded':round(paid,2),'paymentsCount':payments_count,'subscriptionsPending':pending,
            'subscriptionsKnown':known,'subscriptionsMissingDates':missing_dates,'monthlyPending':monthly_pending,
            'subscriptionBreakdown':breakdown}
