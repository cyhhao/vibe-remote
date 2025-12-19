#!/usr/bin/env python3
"""
获取Telegram群组ID的辅助脚本

使用方法：
1. 设置环境变量 TELEGRAM_BOT_TOKEN
2. 运行脚本
3. 把Bot拉进群组并发送一条消息
4. 再次运行脚本，查看群组ID
"""

import os
import requests
import json

def get_updates():
    """获取Bot的最新消息"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ 错误：请设置 TELEGRAM_BOT_TOKEN 环境变量")
        print("   示例：export TELEGRAM_BOT_TOKEN=123456789:ABCdef...")
        return

    url = f"https://api.telegram.org/bot{token}/getUpdates"

    try:
        response = requests.get(url)
        data = response.json()

        if not data.get("ok"):
            print(f"❌ API错误：{data.get('description')}")
            return

        updates = data.get("result", [])

        if not updates:
            print("ℹ️ 没有找到消息")
            print("\n请执行以下步骤：")
            print("1. 把Bot拉进群组")
            print("2. 在群组发送一条消息")
            print("3. 重新运行此脚本")
            return

        print("📋 最近的消息：\n")
        print("-" * 60)

        for update in updates[-10:]:  # 显示最近10条
            update_id = update.get("update_id")
            message = update.get("message", {})
            chat = message.get("chat", {})

            chat_type = chat.get("type")
            chat_id = chat.get("id")
            chat_title = chat.get("title", chat.get("first_name", "Unknown"))
            chat_username = chat.get("username")

            # 格式化输出
            print(f"🆔 Chat ID: {chat_id}")
            print(f"📝 名称: {chat_title}")
            print(f"🔗 类型: {chat_type}")

            if chat_username:
                print(f"👤 用户名: @{chat_username}")

            # 特别标注群组
            if chat_type in ["group", "supergroup"]:
                print("⭐ 群组/超级群组")
                print("   → 复制上面的 Chat ID 到 .env 文件")
                print(f"   → TELEGRAM_TARGET_CHAT_ID={chat_id}")
            elif chat_type == "private":
                print("💬 私聊")

            print("-" * 60)

        # 查找所有群组
        groups = []
        for update in updates:
            chat = update.get("message", {}).get("chat", {})
            if chat.get("type") in ["group", "supergroup"]:
                chat_id = chat.get("id")
                chat_title = chat.get("title", "Unknown")
                if chat_id not in [g["id"] for g in groups]:
                    groups.append({"id": chat_id, "title": chat_title})

        if groups:
            print("\n🎯 找到的群组：")
            for group in groups:
                print(f"  • {group['title']} (ID: {group['id']})")
                print(f"    → TELEGRAM_TARGET_CHAT_ID={group['id']}")

    except Exception as e:
        print(f"❌ 发生错误：{e}")

if __name__ == "__main__":
    print("=" * 60)
    print("🔍 Telegram 群组ID获取工具")
    print("=" * 60)
    print()
    get_updates()
