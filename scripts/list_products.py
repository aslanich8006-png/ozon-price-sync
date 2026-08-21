#!/usr/bin/env python3
"""
Показывает список товаров продавца из Ozon: offer_id (артикул) рядом с product_id (Ozon ID).
Нужен, чтобы понять, какое именно значение использовать в config/prices.csv (нужен offer_id).
"""

import os
import sys
import json
import urllib.request
import urllib.error

API_URL = "https://api-seller.ozon.ru/v2/product/list"


def main():
    client_id = os.environ.get("OZON_CLIENT_ID")
    api_key = os.environ.get("OZON_API_KEY")
    if not client_id or not api_key:
        print("Не заданы OZON_CLIENT_ID / OZON_API_KEY", file=sys.stderr)
        sys.exit(1)

    last_id = ""
    total_printed = 0
    print(f"{'offer_id (артикул)':<25} {'product_id (Ozon ID)':<22} archived")
    print("-" * 60)

    while True:
        body = json.dumps({
            "filter": {"visibility": "ALL"},
            "last_id": last_id,
            "limit": 100,
        }).encode("utf-8")

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

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}", file=sys.stderr)
            sys.exit(1)

        result = data.get("result", {})
        items = result.get("items", [])
        for item in items:
            print(f"{item.get('offer_id',''):<25} {item.get('product_id',''):<22} {item.get('archived')}")
            total_printed += 1

        last_id = result.get("last_id", "")
        if not last_id or not items:
            break

    if total_printed == 0:
        print("Товары не найдены. Проверь, что Client-Id/Api-Key от того же кабинета, где есть товары.")
    else:
        print(f"\nВсего товаров: {total_printed}")


if __name__ == "__main__":
    main()
