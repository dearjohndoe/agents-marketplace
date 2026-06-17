# TODO / известные баги

## BUG: `_process_entry` — `mark_failed_transient` is a no-op в ветках recover-fail и balance-fail

**Где:** `sidecar/api/domain/refund_worker.py`, `_process_entry`
- recover-fail: строки ~201-205 (`could not recover sender/amount...`)
- balance-fail: строки ~213-218 (`balance_check_failed: ...`)

**Что не так:**
Обе ветки выполняются **до** `claim()`, т.е. запись ещё в статусе `pending`.
А `RefundQueue.mark_failed_transient` обновляет только `WHERE status = 'refunding'`:

```sql
UPDATE pending_refunds SET status='pending', last_error=?, next_attempt_at=?
 WHERE tx_hash=? AND status='refunding'
```

→ для `pending`-записи это **no-op**: `last_error` не пишется, `next_attempt_at`
не сдвигается. Рефанд на этом тике корректно пропускается, но:
- бэкофф **не применяется** — запись остаётся due и ретраится на каждом тике
  воркера (нет экспоненциальной паузы, которую расписывает `_BACKOFF_SCHEDULE`);
- `last_error` остаётся пустым → диагностика теряется.

**Эффект:** запись с нерекаверабельным sender/amount или с проваленным
balance-check крутится в горячем цикле fetch_due → _process_entry → no-op, пока
не сработает другой выход (recover/balance восстановятся, либо max_attempts —
но attempts тут тоже не инкрементится, т.к. claim не вызывается).

**Текущее поведение зафиксировано тестами** (характеризация, не «как надо»):
- `tests/test_refund_worker.py::test_process_entry_transient_when_recover_fails`
- `tests/test_refund_worker.py::test_process_entry_transient_when_balance_insufficient`

При исправлении эти два теста нужно обновить под новое (корректное) поведение.

**Возможные фиксы (на выбор, обдумать):**
1. Ввести `mark_pending_backoff(tx_hash, error, backoff)` без guard на статус
   (или `WHERE status IN ('pending','refunding')`) и звать его в этих ветках.
2. Перенести recover/balance проверки **после** `claim()` — тогда запись уже
   `refunding` и существующий `mark_failed_transient` сработает (но claim
   инкрементит attempts — поведение по счётчику попыток изменится).

Связано с этапом 0 multichain-рефактора (refund worker → dispatch по rail_id):
зафиксировать/починить до переноса логики в `chains/`.
