#!/usr/bin/env python3
"""Standalone test: verify Feishu/Lark WebSocket connection + event receiving."""

import json
import sys
import time
import threading

sys.path.insert(0, ".")

from config.v2_config import V2Config


def main():
    config = V2Config.load()
    lark_cfg = config.lark
    if not lark_cfg or not lark_cfg.app_id:
        print("ERROR: No lark config found")
        return

    print(f"App ID:   {lark_cfg.app_id}")
    print(f"Domain:   {lark_cfg.domain}")
    print(f"Base URL: {lark_cfg.api_base_url}")

    import lark_oapi as lark

    domain = lark.LARK_DOMAIN if lark_cfg.domain == "lark" else lark.FEISHU_DOMAIN
    print(f"SDK domain: {domain}")

    # --- Step 1: verify REST API (tenant token) ---
    print("\n--- Step 1: REST API test (get tenant token) ---")
    import requests

    resp = requests.post(
        f"{lark_cfg.api_base_url}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": lark_cfg.app_id, "app_secret": lark_cfg.app_secret},
    )
    token_data = resp.json()
    if token_data.get("code") != 0:
        print(f"FAIL: {token_data}")
        return
    token = token_data["tenant_access_token"]
    print(f"OK: got tenant token (expires in {token_data.get('expire')}s)")

    # --- Step 2: verify bot info ---
    print("\n--- Step 2: Bot info test ---")
    resp2 = requests.get(
        f"{lark_cfg.api_base_url}/open-apis/bot/v3/info",
        headers={"Authorization": f"Bearer {token}"},
    )
    bot_data = resp2.json()
    if bot_data.get("code") != 0:
        print(f"FAIL: {bot_data}")
        return
    bot_open_id = bot_data.get("bot", {}).get("open_id")
    print(f"OK: bot open_id = {bot_open_id}")

    # --- Step 3: WebSocket connection test ---
    print("\n--- Step 3: WebSocket connection test ---")
    print("Building event handler...")

    received_events = []

    def on_message(data):
        print(f"\n*** EVENT RECEIVED ***")
        try:
            if hasattr(data, "header"):
                print(f"  event_type: {data.header.event_type}")
                print(f"  event_id:   {data.header.event_id}")
            if hasattr(data, "event") and data.event:
                event = data.event
                msg = getattr(event, "message", None)
                if msg:
                    print(f"  message_id:   {getattr(msg, 'message_id', '?')}")
                    print(f"  message_type: {getattr(msg, 'message_type', '?')}")
                    print(f"  content:      {getattr(msg, 'content', '?')}")
                    print(f"  chat_type:    {getattr(msg, 'chat_type', '?')}")
                    print(f"  chat_id:      {getattr(msg, 'chat_id', '?')}")
                sender = getattr(event, "sender", None)
                if sender:
                    sid = getattr(sender, "sender_id", None)
                    print(f"  sender_type:  {getattr(sender, 'sender_type', '?')}")
                    if sid:
                        print(f"  open_id:      {getattr(sid, 'open_id', '?')}")
        except Exception as exc:
            print(f"  (parse error: {exc})")
        received_events.append(data)

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()

    print(f"Creating WS client with domain={domain}")
    ws_client = lark.ws.Client(
        app_id=lark_cfg.app_id,
        app_secret=lark_cfg.app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.DEBUG,
        domain=domain,
    )

    ws_error = [None]

    def ws_thread():
        try:
            ws_client.start()
        except Exception as exc:
            ws_error[0] = exc
            print(f"\nWS THREAD ERROR: {exc}")

    t = threading.Thread(target=ws_thread, daemon=True)
    t.start()

    print("Waiting 5s for WS connection...")
    time.sleep(5)

    if ws_error[0]:
        print(f"\nFAILED: WS connection error: {ws_error[0]}")
        return

    if not t.is_alive():
        print("\nFAILED: WS thread died")
        return

    print("\nWS connection appears OK. Waiting for messages...")
    print("Send a message to the bot in Lark/Feishu, then check output here.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\nStopped. Received {len(received_events)} events total.")


if __name__ == "__main__":
    main()
