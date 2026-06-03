import os, logging, re, json, asyncio
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "METS_TON_TOKEN_ICI")
CANAL      = os.environ.get("CANAL", "@Erdezz")
CODE_AFFIL = os.environ.get("CODE_AFFIL", "TTJ7Y")
REMISE     = "60%"

OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
WAIT_PRICE        = 1
WAIT_PRICE_CUSTOM = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# ─── Résolution lien court ────────────────────────────────────────────────────

def resolve_url(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        return r.url
    except Exception as e:
        logging.warning(f"Redirect error: {e}")
        return url

# ─── Nettoyage nom produit ────────────────────────────────────────────────────

def clean_name(raw: str) -> str:
    if not raw:
        return "Produit Shein"
    cut_words = [
        "pour femme", "pour homme", "pour fille", "pour garçon",
        "mode", "casual", "fashion", "style", "women", "men",
        "printemps", "été", "automne", "hiver", "y2k",
        "anniversaire", "noël", "cadeau", "idéal"
    ]
    name_lower = raw.lower()
    cut_pos = len(raw)
    for w in cut_words:
        pos = name_lower.find(w)
        if pos > 20:
            cut_pos = min(cut_pos, pos)
    raw = raw[:cut_pos].strip(" -,|")
    words = raw.split()
    if len(words) > 7:
        raw = " ".join(words[:7])
    return raw.strip(" -,|") or "Produit Shein"

# ─── Scraping principal (requests uniquement) ─────────────────────────────────

def extract_name_from_url(url: str) -> str:
    m = re.search(r'shein\.com/(?:[a-z]{2}/)?(.+?)-p-\d+', url)
    if m:
        slug = m.group(1).replace("-", " ").title()
        return clean_name(slug)
    return "Produit Shein"

async def scrape_shein(url: str):
    name, price, img = None, None, None

    try:
        from bs4 import BeautifulSoup
        session = requests.Session()
        session.get("https://fr.shein.com/", headers=HEADERS, timeout=8)
        r = session.get(url, headers=HEADERS, timeout=15)
        html_text = r.text

        if r.status_code == 200 and len(html_text) > 500:
            soup = BeautifulSoup(html_text, "lxml")
            og = lambda p: (soup.find("meta", property=p) or {}).get("content")
            name  = og("og:title")
            img   = og("og:image")
            price = og("product:price:amount")

            if not price:
                for s in soup.find_all("script", type="application/ld+json"):
                    try:
                        d = json.loads(s.string or "")
                        if isinstance(d, dict) and "offers" in d:
                            price = str(d["offers"].get("price", ""))
                            if not img:
                                img = d.get("image", [None])[0] if isinstance(d.get("image"), list) else d.get("image")
                            break
                    except Exception:
                        pass

            if not price:
                for pattern in [
                    r'"salePrice"[:\s]*\{[^}]*"amount"[:\s]*"?([\d.]+)"?',
                    r'"retailPrice"[:\s]*\{[^}]*"amount"[:\s]*"?([\d.]+)"?',
                    r'\"amount\":\"([\d.]+)\"',
                ]:
                    m2 = re.search(pattern, html_text)
                    if m2:
                        price = m2.group(1)
                        break

    except Exception as e:
        logging.warning(f"Requests scraping error: {e}")

    # Nom fallback depuis l'URL
    if not name:
        name = extract_name_from_url(url)

    # Nettoyage nom
    name = clean_name(name) if name else "Produit Shein"

    # Nettoyage prix
    if price:
        price = re.sub(r'[^\d.,]', '', str(price)).replace(",", ".").strip(".")
        try:
            if price in ("", ".") or float(price) < 0.5:
                price = None
        except ValueError:
            price = None

    return name, price, img

# ─── Formatage message canal ──────────────────────────────────────────────────

def build_caption(name: str, price, url: str) -> str:
    lines = [f"{name.upper()} 😍"]
    if price:
        lines.append(f"Prix : {price}€")
    lines.append(f"-{REMISE} coupon : {CODE_AFFIL} 🏷️")
    lines.append("")
    lines.append(f"👉 {url}")
    return "\n".join(lines)

# ─── Aperçu utilisateur ───────────────────────────────────────────────────────

async def send_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url     = context.user_data["url"]
    name    = context.user_data["name"]
    price   = context.user_data.get("price")
    img     = context.user_data.get("img")
    caption = build_caption(name, price, url)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Publier dans le canal", callback_data="publish"),
        InlineKeyboardButton("❌ Annuler", callback_data="cancel"),
    ]])

    await update.message.reply_text("📋 Aperçu :")

    if img:
        try:
            await update.message.reply_photo(photo=img, caption=caption, reply_markup=keyboard)
            return
        except Exception as e:
            logging.warning(f"Photo preview failed: {e}")

    await update.message.reply_text(caption, reply_markup=keyboard, disable_web_page_preview=False)

# ─── Handler message entrant ──────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Accès refusé.")
        return

    text = update.message.text.strip()

    if context.user_data.get("awaiting_custom_price"):
        context.user_data["awaiting_custom_price"] = False
        m = re.search(r'(\d+[.,]?\d{0,2})', text)
        if m:
            context.user_data["price"] = m.group(1).replace(",", ".")
            await send_preview(update, context)
        else:
            await update.message.reply_text("❌ Envoie un nombre comme 12.99 ou 15")
            context.user_data["awaiting_custom_price"] = True
        return

    url_match = re.search(r'https?://[^\s]*(shein\.com|onelink\.shein\.com)[^\s]*', text)

    if not url_match:
        await update.message.reply_text(
            "👋 Envoie-moi un lien Shein !\n\n"
            "✅ Formats acceptés :\n"
            "• https://fr.shein.com/...\n"
            "• https://onelink.shein.com/...\n\n"
            "💡 Tu peux aussi ajouter le prix dans le message :\n"
            "https://fr.shein.com/... 12.99"
        )
        return

    raw_url = url_match.group(0)

    clean_text = text.replace(raw_url, "").strip()
    price_match = re.search(r'\b(\d+[.,]\d{1,2})\b', clean_text)
    manual_price = price_match.group(1).replace(",", ".") if price_match else None

    await update.message.reply_text("⏳ Récupération des infos en cours...")

    if "onelink.shein.com" in raw_url:
        resolved = resolve_url(raw_url)
        scrape_url = resolved if "shein.com" in resolved and "onelink" not in resolved else raw_url
    else:
        scrape_url = raw_url

    name, scraped_price, img = await scrape_shein(scrape_url)
    price = manual_price or scraped_price

    context.user_data.update({"url": raw_url, "name": name, "price": price, "img": img, "awaiting_custom_price": False})

    if price:
        await send_preview(update, context)
    else:
        await ask_price_keyboard(update, name)

# ─── Clavier de sélection de prix ────────────────────────────────────────────

async def ask_price_keyboard(update: Update, name: str):
    tranches = [
        ["0-5€", "5-10€", "10-15€", "15-20€"],
        ["20-30€", "30-40€", "40-50€", "50-75€"],
        ["75-100€", "100-150€", "150-200€", "200-300€"],
        ["✏️ Saisir le prix manuellement", "🚫 Sans prix"],
    ]
    keyboard = []
    for row in tranches:
        keyboard.append([InlineKeyboardButton(label, callback_data=f"price:{label}") for label in row])

    await update.message.reply_text(
        f"💬 Prix non trouvé pour *{name}*\n\nChoisis une tranche ou saisis le prix :",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ─── Aperçu depuis un callback query ─────────────────────────────────────────

async def send_preview_from_query(query, context: ContextTypes.DEFAULT_TYPE):
    url     = context.user_data["url"]
    name    = context.user_data["name"]
    price   = context.user_data.get("price")
    img     = context.user_data.get("img")
    caption = build_caption(name, price, url)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Publier dans le canal", callback_data="publish"),
        InlineKeyboardButton("❌ Annuler", callback_data="cancel"),
    ]])

    if img:
        try:
            await query.message.reply_photo(photo=img, caption=caption, reply_markup=keyboard)
            return
        except Exception as e:
            logging.warning(f"Photo preview failed: {e}")
    await query.message.reply_text(caption, reply_markup=keyboard, disable_web_page_preview=False)

# ─── Handler bouton Publier / Annuler ─────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.callback_query.answer("⛔ Accès refusé.", show_alert=True)
        return
    query = update.callback_query
    await query.answer()

    if query.data.startswith("price:"):
        choix = query.data[6:]

        if choix == "🚫 Sans prix":
            context.user_data["price"] = None
            await query.edit_message_text("✅ Pas de prix affiché.")
            await send_preview_from_query(query, context)
            return

        if choix == "✏️ Saisir le prix manuellement":
            await query.edit_message_text("✏️ Tape le prix exact (ex: 12.99) :")
            context.user_data["awaiting_custom_price"] = True
            return

        m = re.findall(r'\d+', choix)
        if len(m) == 2:
            mid = (int(m[0]) + int(m[1])) / 2
            context.user_data["price"] = f"{mid:.2f}"
        elif len(m) == 1:
            context.user_data["price"] = m[0] + ".00"

        await query.edit_message_text(f"✅ Prix sélectionné : {context.user_data['price']}€")
        await send_preview_from_query(query, context)
        return

    if query.data == "cancel":
        context.user_data.clear()
        try:
            await query.edit_message_caption(caption="❌ Annulé. Envoie un nouveau lien quand tu veux !")
        except Exception:
            await query.edit_message_text("❌ Annulé. Envoie un nouveau lien quand tu veux !")
        return

    if query.data != "publish":
        return

    url     = context.user_data.get("url")
    name    = context.user_data.get("name")
    price   = context.user_data.get("price")
    img     = context.user_data.get("img")
    caption = build_caption(name, price, url)

    published = False

    if img:
        try:
            await context.bot.send_photo(chat_id=CANAL, photo=img, caption=caption)
            published = True
        except Exception as e:
            logging.warning(f"send_photo canal échoué: {e}")

    if not published:
        try:
            await context.bot.send_message(chat_id=CANAL, text=caption, disable_web_page_preview=False)
            published = True
        except Exception as e:
            logging.error(f"send_message canal échoué: {e}")
            try:
                await query.edit_message_text(
                    f"❌ Erreur publication :\n{e}\n\n"
                    f"Vérifie que le bot est admin de {CANAL} avec le droit Publier des messages."
                )
            except Exception:
                pass
            return

    if published:
        context.user_data.clear()
        try:
            await query.edit_message_caption(caption="🎉 Publié dans le canal ! 🚀\n\nEnvoie un nouveau lien quand tu veux !")
        except Exception:
            try:
                await query.edit_message_text("🎉 Publié dans le canal ! 🚀\n\nEnvoie un nouveau lien quand tu veux !")
            except Exception:
                pass

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("🤖 Bot Shein v6 démarré !")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
