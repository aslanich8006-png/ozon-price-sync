#!/usr/bin/env python3
"""
Телеграм-бот для управления синхронизацией цен Ozon по команде.

Не меняет цены сам по себе — только реагирует на команды:
  /start или /help   - подсказка по командам
  /sync              - пересчитать и отправить в Ozon ВСЕ цены из config/prices.csv
  /prices            - показать список товаров и текущих цен из config/prices.csv
  <артикул> <цена>   - изменить цену одного товара и сразу отправить в Ozon
                        (например: "SHETKA-7-OZON 1200")

Запускается мгновенно через Cloudflare Worker (вебхук Telegram), который передаёт
текст сообщения и chat_id напрямую как параметры запуска — getUpdates не используется,
т.к. конфликтует с активным вебхуком (Telegram отдаёт 409 Conflict).
"""

import csv
import os
import sys
import json
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
from sync_prices import compute_price, call_ozon, chunks, CSV_PATH  # noqa: E402

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def tg_call(token, method, params=None):
    url = TELEGRAM_API.format(token=token, method=method)
    data = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message(token, chat_id, text):
    for i in range(0, len(text), 3500):
        tg_call(token, "sendMessage", {"chat_id": chat_id, "text": text[i:i + 3500]})


def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(rows, fieldnames):
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def handle_sync(token, chat_id, client_id, api_key):
    rows = load_rows()
    entries, errors = [], []
    for row in rows:
        try:
            entries.append(compute_price(row))
        except ValueError as e:
            errors.append(str(e))

    if errors:
        send_message(token, chat_id, "Ошибки в prices.csv:\n" + "\n".join(errors))
        return

    ok, fail = [], []
    for batch in chunks(entries, 100):
        result = call_ozon(batch, client_id, api_key)
        for item in result.get("result", []):
            if item.get("updated"):
                ok.append(item.get("offer_id"))
            else:
                fail.append(f"{item.get('offer_id')}: {item.get('errors')}")

    lines = [f"Синхронизация завершена: {len(ok)} ок, {len(fail)} с ошибкой."]
    if fail:
        lines.append("Ошибки:")
        lines.extend(fail)
    send_message(token, chat_id, "\n".join(lines))


def handle_prices(token, chat_id):
    rows = load_rows()
    if not rows:
        send_message(token, chat_id, "config/prices.csv пуст.")
        return
    lines = ["Товары в prices.csv:"]
    for row in rows:
        if row.get("mode") == "margin":
            desc = f"наценка {row.get('value')}% от себестоимости {row.get('cost_price')}"
        else:
            desc = f"{row.get('value')} руб"
        lines.append(f"- {row.get('offer_id')}: {desc}")
    send_message(token, chat_id, "\n".join(lines))


def handle_set_price(token, chat_id, client_id, api_key, offer_id, price):
    rows = load_rows()
    fieldnames = ["offer_id", "mode", "value", "cost_price", "old_price"]
    found = False
    for row in rows:
        if row.get("offer_id", "").strip() == offer_id:
            row["mode"] = "fixed"
            row["value"] = str(price)
            found = True
            break
    if not found:
        rows.append({
            "offer_id": offer_id, "mode": "fixed", "value": str(price),
            "cost_price": "", "old_price": "",
        })
    save_rows(rows, fieldnames)

    entry = compute_price({"offer_id": offer_id, "mode": "fixed", "value": str(price), "cost_price": "", "old_price": ""})
    result = call_ozon([entry], client_id, api_key)
    items = result.get("result", [])
    if items and items[0].get("updated"):
        send_message(token, chat_id, f"Готово: {offer_id} -> {price} руб (сохранено в prices.csv и отправлено в Ozon).")
    else:
        errs = items[0].get("errors") if items else result
        send_message(token, chat_id, f"Ozon не принял цену для {offer_id}: {errs}\n(в prices.csv значение всё равно сохранено)")


def process_message(token, allowed_chat_id, client_id, api_key, chat_id, text):
    if chat_id != str(allowed_chat_id):
        print(f"Игнорирую сообщение из чужого чата {chat_id}")
        return

    print(f"Команда: {text!r}")

    if text in ("/start", "/help"):
        send_message(token, chat_id,
            "Команды:\n"
            "/sync - синхронизировать все цены с Ozon\n"
            "/prices - показать текущие цены\n"
            "<артикул> <цена> - изменить цену одного товара, например:\n"
            "SHETKA-7-OZON 1200")
    elif text == "/sync":
        handle_sync(token, chat_id, client_id, api_key)
    elif text == "/prices":
        handle_prices(token, chat_id)
    else:
        parts = text.rsplit(" ", 1)
        offer_id, price = None, None
        if len(parts) == 2:
            try:
                price = float(parts[1])
                offer_id = parts[0].strip()
            except ValueError:
                pass
        if offer_id and price is not None:
            handle_set_price(token, chat_id, client_id, api_key, offer_id, price)
        else:
            send_message(token, chat_id, "Не понял команду. Напиши /help")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    client_id = os.environ.get("OZON_CLIENT_ID")
    api_key = os.environ.get("OZON_API_KEY")

    if not token or not allowed_chat_id:
        print("Не заданы TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", file=sys.stderr)
        sys.exit(1)

    input_text = os.environ.get("INPUT_TEXT", "").strip()
    input_chat_id = os.environ.get("INPUT_CHAT_ID", "").strip()

    if input_text and input_chat_id:
        process_message(token, allowed_chat_id, client_id, api_key, input_chat_id, input_text)
        return

    # Фолбэк для ручного запуска без параметров (используется редко, для отладки).
    updates = tg_call(token, "getUpdates", {"timeout": 0})
    results = updates.get("result", [])

    if not results:
        print("Новых сообщений нет.")
        return

    last_update_id = 0
    for upd in results:
        last_update_id = max(last_update_id, upd["update_id"])
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            continue
        chat_id = str(msg.get("chat", {}).get("id"))
        text = (msg.get("text") or "").strip()
        process_message(token, allowed_chat_id, client_id, api_key, chat_id, text)

    tg_call(token, "getUpdates", {"offset": last_update_id + 1, "timeout": 0})


if __name__ == "__main__":
    main()
