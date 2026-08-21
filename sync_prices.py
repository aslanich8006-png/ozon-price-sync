#!/usr/bin/env python3
"""
Синхронизация цен товаров с Ozon Seller API.

Источник цен — config/prices.csv, две поддерживаемые схемы на строку:

  mode=fixed   -> цена берётся из колонки value как есть
  mode=margin  -> цена = cost_price * (1 + value/100), value = наценка в %

Колонки CSV:
  offer_id    - твой SKU/артикул в Ozon (обязательно)
  mode        - fixed | margin (обязательно)
  value       - цена (для fixed) или % наценки (для margin) (обязательно)
  cost_price  - себестоимость, нужна только для mode=margin
  old_price   - "старая" (зачёркнутая) цена, необязательно

Ключи Client-Id / Api-Key берутся из переменных окружения
OZON_CLIENT_ID и OZON_API_KEY (в GitHub Actions прокидываются из Secrets).

DRY_RUN=true - посчитать и вывести цены, не отправляя запрос в Ozon.
"""

import csv
import os
import sys
import time
import json
import urllib.request
import urllib.error

API_URL = "https://api-seller.ozon.ru/v1/product/import/prices"
CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "prices.csv")
BATCH_SIZE = 100
CURRENCY_CODE = "RUB"


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path} пуст или не найден")
    return rows


def compute_price(row):
    offer_id = (row.get("offer_id") or "").strip()
    mode = (row.get("mode") or "").strip().lower()
    value = (row.get("value") or "").strip()
    cost_price = (row.get("cost_price") or "").strip()
    old_price = (row.get("old_price") or "").strip()

    if not offer_id:
        raise ValueError(f"Строка без offer_id: {row}")
    if mode not in ("fixed", "margin"):
        raise ValueError(f"{offer_id}: неизвестный mode '{mode}' (ожидается fixed или margin)")
    if not value:
        raise ValueError(f"{offer_id}: пустое значение value")

    value_f = float(value)

    if mode == "fixed":
        price = value_f
    else:  # margin
        if not cost_price:
            raise ValueError(f"{offer_id}: mode=margin требует заполненного cost_price")
        cost_f = float(cost_price)
        price = cost_f * (1 + value_f / 100)

    price = round(price)  # Ozon оперирует ценами в рублях без копеек

    entry = {
        "offer_id": offer_id,
        "price": str(price),
        "currency_code": CURRENCY_CODE,
    }
    if old_price:
        entry["old_price"] = str(round(float(old_price)))

    return entry


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def call_ozon(batch, client_id, api_key):
    body = json.dumps({"prices": batch}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json",
        },
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}: {detail}"
        except urllib.error.URLError as e:
            last_err = f"Сетевая ошибка: {e.reason}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Не удалось обновить цены после 3 попыток. Последняя ошибка: {last_err}")


def main():
    dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

    rows = load_rows(CSV_PATH)

    entries = []
    errors = []
    for row in rows:
        try:
            entries.append(compute_price(row))
        except ValueError as e:
            errors.append(str(e))

    if errors:
        print("Ошибки в config/prices.csv:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Посчитано {len(entries)} цен:")
    for e in entries:
        extra = f", old_price={e['old_price']}" if "old_price" in e else ""
        print(f"  {e['offer_id']}: {e['price']} {e['currency_code']}{extra}")

    if dry_run:
        print("\nDRY_RUN=true — запрос в Ozon не отправлялся.")
        return

    client_id = os.environ.get("OZON_CLIENT_ID")
    api_key = os.environ.get("OZON_API_KEY")
    if not client_id or not api_key:
        print("Не заданы OZON_CLIENT_ID / OZON_API_KEY", file=sys.stderr)
        sys.exit(1)

    had_failures = False
    for batch in chunks(entries, BATCH_SIZE):
        result = call_ozon(batch, client_id, api_key)
        for item in result.get("result", []):
            status = "OK" if item.get("updated") else "FAIL"
            print(f"  [{status}] offer_id={item.get('offer_id')}")
            if not item.get("updated"):
                had_failures = True
                for err in item.get("errors", []):
                    print(f"        -> {err}")

    if had_failures:
        print("\nЕсть товары, цену которых Ozon не принял (см. FAIL выше).", file=sys.stderr)
        sys.exit(1)

    print("\nГотово: все цены обновлены.")


if __name__ == "__main__":
    main()
