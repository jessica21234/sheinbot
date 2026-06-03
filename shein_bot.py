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

# ─── Ton ID Telegram — seul toi peux utiliser le bot ─────────────────────────
# Pour trouver ton ID : envoie /start à @userinfobot sur Telegram
OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))  # remplace 0 par ton ID

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
WAIT_PRICE        = 1
WAIT_PRICE_CUSTOM = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}

HEADERS_MOBILE = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
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
    """
    Shein met des noms ultra longs avec des mots-clés SEO.
    On garde seulement les 6 premiers mots significatifs.
    Ex: "SHEGLAM Longwear Invisible Hold Colle Pour Cils-Clear Marque..."
    → "SHEGLAM Longwear Invisible Hold Colle Pour Cils"
    """
    if not raw:
        return "Produit Shein"
    # Supprimer les parties génériques après certains mots-clés
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
        if pos > 20:  # pas au tout début
            cut_pos = min(cut_pos, pos)
    raw = raw[:cut_pos].strip(" -,|")

    # Garder max 7 mots
    words = raw.split()
    if len(words) > 7:
        raw = " ".join(words[:7])

    return raw.strip(" -,|") or "Produit Shein"

# ─── Scraping avec Playwright (rendu JS) ─────────────────────────────────────

def extract_price_from_html(html: str) -> str | None:
    """Extraction prix agressive depuis le HTML brut — patterns étendus."""
    patterns = [
        # JSON structuré Shein
        r'"salePrice"\s*:\s*\{\s*[^}]*"amount"\s*:\s*"?([\d.]+)"?',
        r'"retailPrice"\s*:\s*\{\s*[^}]*"amount"\s*:\s*"?([\d.]+)"?',
        r'"currentPrice"\s*:\s*\{\s*[^}]*"amount"\s*:\s*"?([\d.]+)"?',
        r'"discountPrice"\s*:\s*\{\s*[^}]*"amount"\s*:\s*"?([\d.]+)"?',
        r'"priceInfo"\s*:\s*\{\s*[^}]*"salePrice"\s*:\s*"?([\d.]+)"?',
        r'"amount"\s*:\s*"([\d]{1,3}[.,]\d{2})"',
        r'"amount"\s*:\s*([\d]{1,3}\.\d{2})',
        # JSON-LD schema.org
        r'"price"\s*:\s*"([\d]{1,3}[.,]\d{2})"',
        r'"price"\s*:\s*([\d]{1,3}\.\d{2})',
        r'"lowPrice"\s*:\s*"?([\d.]+)"?',
        r'"highPrice"\s*:\s*"?([\d.]+)"?',
        # Attributs HTML
        r'data-price="([\d.]+)"',
        r'data-sale-price="([\d.]+)"',
        r'itemprop="price"\s+content="([\d.]+)"',
        r'content="([\d]{1,3}\.\d{2})"\s+itemprop="price"',
        # Texte brut avec symbole €
        r'([\d]{1,3}[.,]\d{2})\s*€',
        r'€\s*([\d]{1,3}[.,]\d{2})',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            val = m.group(1).replace(",", ".").strip()
            try:
                if 0.5 < float(val) < 10000:
                    return val
            except ValueError:
                continue
    return None


async def scrape_with_playwright(url: str) -> dict:
    """Scraping complet avec navigateur headless — version améliorée."""
    result = {"name": None, "price": None, "image": None}
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-web-security",
                ]
            )
            # Contexte desktop
            ctx = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="fr-FR",
                extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"},
                viewport={"width": 1280, "height": 800},
            )
            # Masquer le fait qu'on est un bot
            await ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)
            page = await ctx.new_page()

            # Bloquer les ressources inutiles pour aller plus vite
            await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,mp4,mp3}", lambda r: r.abort())

            await page.goto(url, wait_until="domcontentloaded", timeout=35000)
            await page.wait_for_timeout(5000)

            html = await page.content()

            # ── Nom ──
            og_title = await page.evaluate("() => document.querySelector('meta[property=\"og:title\"]')?.content")
            h1 = await page.evaluate("() => document.querySelector('h1')?.innerText")
            result["name"] = og_title or h1

            # ── Image ──
            og_img = await page.evaluate("() => document.querySelector('meta[property=\"og:image\"]')?.content")
            result["image"] = og_img

            # ── Prix via sélecteurs CSS (mise à jour 2024-2025) ──
            price_selectors = [
                # Sélecteurs récents Shein
                "[class*='ProductIntroHeadPrice'] [class*='from']",
                "[class*='ProductIntroHeadPrice'] [class*='sale']",
                "[class*='product-intro__head-price'] .from",
                "[class*='product-price'] [class*='sale']",
                "[class*='productPrice'] [class*='sale']",
                # Anciens sélecteurs
                ".product-intro__head-price .from",
                ".product-intro__head-price .origin",
                "[class*='sale-price']",
                "[data-test='price']",
                ".price-wrapper .price",
                ".j-sa-product-detail-price",
                # Sélecteurs génériques avec €
                "[class*='price']:not([class*='original']):not([class*='del'])",
            ]
            for sel in price_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        txt = await el.inner_text()
                        m = re.search(r'[\d]+[.,]\d{2}', txt.replace(" ", "").replace("\xa0", ""))
                        if m:
                            val = m.group(0).replace(",", ".")
                            if float(val) > 0.5:
                                result["price"] = val
                                break
                except Exception:
                    pass

            # ── Fallback : extraction depuis le HTML brut ──
            if not result["price"]:
                result["price"] = extract_price_from_html(html)

            # ── Fallback : JSON dans les balises script ──
            if not result["price"]:
                scripts = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                              .map(s => s.textContent)
                """)
                for s in scripts:
                    try:
                        d = json.loads(s or "")
                        offers = d.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0]
                        p = offers.get("price") or offers.get("lowPrice")
                        if p:
                            result["price"] = str(p).replace(",", ".")
                            break
                    except Exception:
                        pass

            await browser.close()
            logging.info(f"Playwright result: {result}")
    except Exception as e:
        logging.warning(f"Playwright error: {e}")
    return result

# ─── Scraping principal ───────────────────────────────────────────────────────

def extract_name_from_url(url: str) -> str:
    m = re.search(r'shein\.com/(?:[a-z]{2}/)?(.+?)-p-\d+', url)
    if m:
        slug = m.group(1).replace("-", " ").title()
        return clean_name(slug)
    return "Produit Shein"

async def scrape_shein(url: str):
    """Essaie requests (desktop puis mobile), puis Playwright si prix manquant."""
    name, price, img = None, None, None

    # ── Tentative 1 : requests desktop ──
    try:
        import requests as req
        from bs4 import BeautifulSoup
        session = req.Session()
        session.headers.update(HEADERS)
        session.get("https://fr.shein.com/", timeout=8)
        r = session.get(url, timeout=15)
        html_text = r.text

        if r.status_code == 200 and len(html_text) > 500:
            soup = BeautifulSoup(html_text, "lxml")
            og = lambda p: (soup.find("meta", property=p) or {}).get("content")
            name  = og("og:title")
            img   = og("og:image")
            price = og("product:price:amount")

            # JSON-LD schema.org
            if not price:
                for s in soup.find_all("script", type="application/ld+json"):
                    try:
                        d = json.loads(s.string or "")
                        offers = d.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0]
                        p = offers.get("price") or offers.get("lowPrice")
                        if p:
                            price = str(p)
                            if not img:
                                imgs = d.get("image", [])
                                img = imgs[0] if isinstance(imgs, list) and imgs else d.get("image")
                            break
                    except Exception:
                        pass

            # Extraction HTML brut
            if not price:
                price = extract_price_from_html(html_text)
    except Exception as e:
        logging.warning(f"Requests desktop scraping error: {e}")

    # ── Tentative 2 : requests mobile (Shein renvoie parfois plus d'infos) ──
    if not price:
        try:
            import requests as req
            session2 = req.Session()
            session2.headers.update(HEADERS_MOBILE)
            r2 = session2.get(url, timeout=15)
            if r2.status_code == 200 and len(r2.text) > 500:
                price = extract_price_from_html(r2.text)
                if price:
                    logging.info(f"Prix trouvé via user-agent mobile : {price}")
        except Exception as e:
            logging.warning(f"Requests mobile scraping error: {e}")

    # ── Tentative 3 : Playwright (navigateur headless complet) ──
    if not price or not name:
        logging.info("Lancement Playwright pour extraction complète...")
        pw = await scrape_with_playwright(url)
        if not name:
            name = pw.get("name")
        if not price:
            price = pw.get("price")
        if not img:
            img = pw.get("image")

    # Nettoyage nom
    name = clean_name(name) if name else extract_name_from_url(url)

    # Nettoyage prix
    if price:
        price = re.sub(r'[^\d.,]', '', str(price)).replace(",", ".").strip(".")
        if price in ("", ".") or float(price) < 0.5:
            price = None

    return name, price, img

# ─── Formatage message canal ──────────────────────────────────────────────────

def build_caption(name: str, price, url: str) -> str:
    """
    Format comme la capture :
    NOM DU PRODUIT 😍
    Prix : 12.99€
    -60% coupon : TU87V 🏷️

    👉 https://...
    """
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
    # Bloquer tout le monde sauf toi
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Accès refusé.")
        return

    text = update.message.text.strip()

    # Si on attend une saisie manuelle de prix
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

    # Prix dans le message ?
    clean_text = text.replace(raw_url, "").strip()
    price_match = re.search(r'\b(\d+[.,]\d{1,2})\b', clean_text)
    manual_price = price_match.group(1).replace(",", ".") if price_match else None

    await update.message.reply_text("⏳ Récupération des infos en cours...")

    # Résoudre lien onelink
    if "onelink.shein.com" in raw_url:
        resolved = resolve_url(raw_url)
        scrape_url = resolved if "shein.com" in resolved and "onelink" not in resolved else raw_url
    else:
        scrape_url = raw_url

    name, scraped_price, img = await scrape_shein(scrape_url)
    price = manual_price or scraped_price

    # Toujours conserver le lien original court pour l'affichage
    context.user_data.update({"url": raw_url, "name": name, "price": price, "img": img, "awaiting_custom_price": False})

    if price:
        await send_preview(update, context)
    else:
        await ask_price_keyboard(update, name)

# ─── Clavier de sélection de prix ────────────────────────────────────────────

async def ask_price_keyboard(update: Update, name: str):
    """Envoie un clavier inline avec des tranches de prix rapides."""
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

# ─── Handler prix manuel (saisie texte après avoir cliqué "Saisir manuellement") ─

async def receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Gardé pour compatibilité, la logique est dans handle_message
    pass


# ─── Aperçu depuis un callback query (pas un message) ────────────────────────

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

    # ── Sélection de prix via clavier ──────────────────────────────────────────
    if query.data.startswith("price:"):
        choix = query.data[6:]  # ex: "10-15€" ou "✏️ Saisir..." ou "🚫 Sans prix"

        if choix == "🚫 Sans prix":
            context.user_data["price"] = None
            await query.edit_message_text("✅ Pas de prix affiché.")
            await send_preview_from_query(query, context)
            return

        if choix == "✏️ Saisir le prix manuellement":
            await query.edit_message_text("✏️ Tape le prix exact (ex: 12.99) :")
            context.user_data["awaiting_custom_price"] = True
            return

        # Tranche choisie → prendre la valeur du milieu
        m = re.findall(r'\d+', choix)
        if len(m) == 2:
            mid = (int(m[0]) + int(m[1])) / 2
            context.user_data["price"] = f"{mid:.2f}"
        elif len(m) == 1:
            context.user_data["price"] = m[0] + ".00"

        await query.edit_message_text(f"✅ Prix sélectionné : {context.user_data['price']}€")
        await send_preview_from_query(query, context)
        return

    # ── Publier / Annuler ──────────────────────────────────────────────────────
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

    # Pas de ConversationHandler — handlers simples, jamais bloquants
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("🤖 Bot Shein v6 démarré !")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
