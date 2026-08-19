#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
淘宝自动比价助手 v0.1
- session: 启动 Edge -> 检查登录 -> 未登录则扫码 -> 保存 cookie
- search : 搜索关键词 -> 保存 HTML 样本 -> 尝试解析商品列表 -> 输出 JSON
用法:
  python taobao_bot.py session                 # 登录并保存会话
  python taobao_bot.py check                   # 检查登录态
  python taobao_bot.py search "关键词" [--limit 15] [--dump]
  python taobao_bot.py parse <html文件>        # 单独解析已保存的 HTML（调试用）
"""
import argparse
import asyncio
import json
import math
import re
import statistics
import sys
import time
import random
import urllib.parse
from pathlib import Path

from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
DATA_DIR = BASE_DIR / "data"
STATE_FILE = STATE_DIR / "taobao_state.json"
STATE_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)


def log(*args):
    print(f"[{time.strftime('%H:%M:%S')}]", *args, flush=True)


async def human_delay(a=0.8, b=2.2):
    await asyncio.sleep(random.uniform(a, b))


async def launch(pw, headless=False):
    """启动 Edge + 恢复/新建登录态"""
    browser = await pw.chromium.launch(
        channel="msedge",
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = await browser.new_context(
        storage_state=str(STATE_FILE) if STATE_FILE.exists() else None,
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    page = await ctx.new_page()
    return browser, ctx, page


def save_state(ctx):
    import asyncio as _a
    state = _a.get_event_loop().run_until_complete(
        _a.ensure_future(ctx.storage_state())
    ) if False else None
    # 上面这行不用；storage_state 是 async 方法，在调用处 await
    return state


async def is_logged_in(page):
    """访问淘宝首页，判断是否已登录，返回 (是否登录, 昵称或None)"""
    try:
        await page.goto("https://www.taobao.com/", wait_until="domcontentloaded", timeout=45000)
        await human_delay(1.0, 2.0)
        html = await page.content()
        if "亲，请登录" in html and "site-nav-user" not in html:
            return False, None
        m = re.search(r'"nick"\s*:\s*"([^"]{1,30})"', html)
        if m:
            return True, m.group(1)
        # 顶部昵称元素兜底
        nick = await page.locator(".site-nav-login-info-nick").first.text_content(timeout=3000) \
            if await page.locator(".site-nav-login-info-nick").count() else None
        return (True, nick.strip()) if nick else (False, None)
    except Exception as e:
        log("登录态检测异常:", e)
        return False, None


async def do_login(page):
    """打开扫码登录页，轮询等待用户扫码（最多3分钟）"""
    await page.goto("https://login.taobao.com/member/login.jhtml", wait_until="domcontentloaded", timeout=60000)
    await human_delay(1.0, 2.0)
    log("请用手机淘宝 App 扫码登录，最多等待 3 分钟……")
    deadline = time.time() + 180
    while time.time() < deadline:
        url = page.url
        if "login" not in url or "taobao.com" not in url:
            # 登录页已跳走，说明扫码成功
            await human_delay(2.0, 3.0)
            ok, nick = await is_logged_in(page)
            if ok:
                return True, nick
        # 某些情况下登录页不跳转但已登录，直接探测
        ok, nick = await is_logged_in(page)
        if ok:
            return True, nick
        await asyncio.sleep(3)
    return False, None


import html as _html


def parse_search_html(html: str, limit: int = 15):
    """从桌面版搜索页 HTML 提取商品。

    基于 2026 实测结构：商品卡片以 id="item_id_<nid>" 为锚点，
    标题在 title-- 元素的 title 属性，价格拆为 priceInt/priceFloat，
    销量在 realSales--，店铺在 shopName--。
    """
    items = []
    # 锚点：商品链接（href 中的 id 即商品 nid；simba 广告无 item.htm 自动排除）
    link_pat = re.compile(
        r'href="//(item\.taobao\.com|detail\.tmall\.com)/item\.htm\?id=(\d+)[^"]*"'
    )
    for lm in link_pat.finditer(html):
        domain, nid = lm.group(1), lm.group(2)
        # 卡片起点 = 最近的 <a 标签开始（href 在其后约 200-400 字符处）
        a_start = html.rfind("<a", max(0, lm.start() - 600), lm.start())
        if a_start == -1:
            a_start = lm.start()
        card = html[a_start: a_start + 8000]

        tm = re.search(r'class="[^"]*title--[^"]*"[^>]*title="([^"]*)"', card)
        title = _html.unescape(tm.group(1)).strip() if tm else ""

        im = re.search(r'class="[^"]*priceInt--[^"]*"[^>]*>([\d.,]+)<', card)
        fm = re.search(r'class="[^"]*priceFloat--[^"]*"[^>]*>([\d.]+)<', card)
        price = (im.group(1) if im else "") + (fm.group(1) if fm else "")

        sm = re.search(r'class="[^"]*realSales--[^"]*"[^>]*>([^<]+)<', card)
        sales = _html.unescape(sm.group(1)).strip() if sm else ""

        sh = re.search(r'class="[^"]*shopNameText--[^"]*"[^>]*>([^<]+)<', card)
        shop = _html.unescape(sh.group(1)).strip() if sh else ""

        is_tmall = domain == "detail.tmall.com"
        url = f"https://{domain}/item.htm?id={nid}"

        items.append({
            "nid": nid,
            "title": title,
            "price": price,
            "sales": sales,
            "shop": shop,
            "is_tmall": is_tmall,
            "url": url,
        })
        if len(items) >= limit:
            break
    return items


async def cmd_session(pw):
    browser, ctx, page = await launch(pw)
    try:
        ok, nick = await is_logged_in(page)
        if ok:
            log(f"已登录: {nick}")
        else:
            ok, nick = await do_login(page)
            if not ok:
                log("登录超时或失败")
                return 1
            log(f"登录成功: {nick}")
        state = await ctx.storage_state()
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        log(f"会话已保存: {STATE_FILE}")
        return 0
    finally:
        await browser.close()


async def cmd_check(pw):
    browser, ctx, page = await launch(pw)
    try:
        ok, nick = await is_logged_in(page)
        if ok:
            log(f"登录态有效: {nick}")
            return 0
        log("未登录")
        return 1
    finally:
        await browser.close()


async def cmd_search(pw, keyword, limit, dump):
    browser, ctx, page = await launch(pw)
    try:
        ok, nick = await is_logged_in(page)
        if not ok:
            log("未登录，先运行: python taobao_bot.py session")
            return 2
        log(f"登录用户: {nick}，搜索: {keyword}")
        url = "https://s.taobao.com/search?q=" + urllib.parse.quote(keyword)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await human_delay(2.0, 4.0)
        # 滚两屏触发懒加载
        for _ in range(2):
            await page.mouse.wheel(0, 1200)
            await human_delay(1.0, 2.0)
        html = await page.content()
        ts = time.strftime("%Y%m%d_%H%M%S")
        hfile = DATA_DIR / f"search_{ts}.html"
        hfile.write_text(html, encoding="utf-8")
        log(f"HTML 样本已保存: {hfile}")
        items = parse_search_html(html, limit)
        log(f"解析到 {len(items)} 个商品")
        out = DATA_DIR / f"items_{ts}.json"
        out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        for it in items:
            print(f"- {it['title'][:40]} | ¥{it['price']} | 销量{it['sales']} | {it['shop']}")
        return 0
    finally:
        await browser.close()


def sku_matches(text: str, spec: str) -> bool:
    """规格匹配：M8*30 匹配 M8*30/M8x30/M8×30/M8*30【10个】，不匹配 M8*300"""
    if not spec:
        return True
    s = spec.lower().replace("×", "*").replace("x", "*")
    norm = text.lower().replace("×", "*").replace("x", "*")
    idx = norm.find(s)
    while idx != -1:
        after = norm[idx + len(s):]
        if not (after and after[0].isdigit()):
            return True
        idx = norm.find(s, idx + 1)
    return False


def parse_sku_count(text: str):
    """从 SKU 文本提取每包数量：'M8*30【100个】'→100，'100只装'→100，'250个/箱'→250；无则 None"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:个|只|颗|粒|条|枚|片|件|pcs|PCS|装|包)", text)
    if m:
        try:
            v = float(m.group(1))
            return int(v) if v.is_integer() else v
        except ValueError:
            return None
    return None


async def _click_sku_by_text(page, text: str) -> bool:
    try:
        loc = page.locator("[class*='valueItem']", has_text=text).first
        await loc.click(timeout=5000)
        await asyncio.sleep(random.uniform(1.0, 2.0))
        return True
    except Exception:
        return False


async def _click_sku_js(page, text):
    """JS 点击文本精确匹配的叶子元素（不依赖类名）"""
    try:
        ok = await page.evaluate("""(text) => {
            const els = [...document.querySelectorAll('li,span,div,button,a')];
            const e = els.find(x => x.children.length === 0 &&
                (x.innerText || '').trim() === text && x.offsetParent !== null);
            if (e) { e.click(); return true; }
            return false;
        }""", text)
        await asyncio.sleep(random.uniform(1.0, 2.0))
        return ok
    except Exception:
        return False


async def pick_sku(page, spec, pack, total_count):
    """智能选 SKU（JS 通用版，不依赖页面类名）：
    凑够需求总量且浪费最少（向上取整，绝不缺量）。
    返回 (sku_text, per_pack, qty)；无匹配 (None, None, None)"""
    data = await page.evaluate("""(args) => {
        const {spec, pack} = args;
        const norm = (s) => s.toLowerCase().replace(/[×xX]/g, '*');
        const s = norm(spec);
        const matches = (t) => {
            const n = norm(t);
            let i = n.indexOf(s);
            while (i !== -1) {
                const after = n[i + s.length] || '';
                if (!/\\d/.test(after)) return true;
                i = n.indexOf(s, i + 1);
            }
            return false;
        };
        const els = [...document.querySelectorAll('li,span,div,button,a')];
        const opts = [];
        for (const e of els) {
            if (e.children.length > 0) continue;   // 只要叶子
            if (e.offsetParent === null) continue; // 不可见跳过
            const t = (e.innerText || '').trim();
            if (!t || t.length > 60) continue;
            if (matches(t)) opts.push(t);
        }
        return [...new Set(opts)];
    }""", {"spec": spec, "pack": pack or ""})
    if not data:
        return None, None, None

    best = None
    for t in data:
        cnt = parse_sku_count(t)
        if cnt:
            packs_needed = max(1, math.ceil(total_count / cnt)) if total_count else 1
            waste = (packs_needed * cnt - total_count) if total_count else 0
            pack_ok = 0 if (pack and pack in t) else 1
            key = (waste, pack_ok, -cnt)
            if best is None or key < best[0]:
                best = (key, t, cnt, packs_needed)
    if best:
        _, t, cnt, packs_needed = best
        await _click_sku_js(page, t)
        return t, cnt, packs_needed

    # 全部解析不出包数：选匹配 pack 的，否则第一个
    target = next((t for t in data if pack and pack in t), data[0])
    await _click_sku_js(page, target)
    if total_count:
        return target, None, total_count  # 按单件计，买够总量
    return target, None, 1


async def set_qty(page, qty):
    """设置购买数量（三级兜底：fill → JS native setter → JS 点加号）"""
    if qty <= 1:
        return
    # 1) Playwright fill（数量输入框是 countValue 类）
    try:
        inp = page.locator(
            "input[class*='quantity'], input[class*='Quantity'], [class*='countValue'], .quantityInput input"
        ).first
        await inp.fill(str(qty), timeout=3000)
        await asyncio.sleep(random.uniform(0.8, 1.5))
        return
    except Exception:
        pass
    # 2) JS native setter（React 受控组件需要 native setter + input/change 事件）
    try:
        ok = await page.evaluate("""(q) => {
            const inp = document.querySelector("[class*='countValue'], input[class*='quantity'], input[class*='Quantity']");
            if (!inp) return false;
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(inp, String(q));
            inp.dispatchEvent(new Event('input', {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""", qty)
        if ok:
            await asyncio.sleep(random.uniform(0.8, 1.5))
            return
    except Exception:
        pass
    # 3) JS 直接点加号（绕过浮层遮挡）
    try:
        await page.evaluate("""(n) => {
            const btn = document.querySelector("[class*='addBtn']");
            if (!btn) return false;
            for (let i = 0; i < n; i++) btn.click();
            return true;
        }""", min(qty - 1, 400))
        await asyncio.sleep(random.uniform(1.0, 2.0))
    except Exception:
        pass


async def click_sku(page, sku_text, sku_pack=None):
    """点击 SKU 选项。优先匹配包装（如"100个"），再匹配规格（如 M8*30）。
    返回选中 SKU 的文本（用于解析每包数量），失败返回 None"""
    candidates = []
    if sku_pack:
        candidates.append(sku_pack)
    if sku_text:
        candidates.append(sku_text)
    for c in candidates:
        try:
            loc = page.locator("[class*='valueItem']", has_text=c).first
            if await loc.count():
                txt = (await loc.inner_text()).strip()
                await loc.click(timeout=5000)
                await asyncio.sleep(random.uniform(1.0, 2.0))
                return txt
        except Exception:
            continue
    return None


async def click_buy_now(page):
    """精确定位并点击"立即购买"主按钮（避免点到推荐位的同名文本）"""
    # 策略1: 底部购买栏容器（EmphasizeButtonList）
    try:
        btn = page.locator("[class*='EmphasizeButtonList']", has_text="立即购买").first
        await btn.click(timeout=5000)
        return True
    except Exception:
        pass
    # 策略2: JS 找文本精确为"立即购买"的叶子元素
    try:
        ok = await page.evaluate("""() => {
            const els = [...document.querySelectorAll('div,span,button,a')];
            const leaf = els.find(e =>
                e.textContent.trim() === '立即购买' && e.children.length === 0 &&
                e.offsetParent !== null);
            if (leaf) { leaf.click(); return true; }
            return false;
        }""")
        if ok:
            return True
    except Exception:
        pass
    # 策略3: 宽松文本点击
    await page.get_by_text("立即购买", exact=True).first.click(timeout=5000)
    return True


async def _snapshot(page, tag):
    """出错时保存现场（正文文本+截图），用于排查"""
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        snap = await page.inner_text("body")
        f1 = DATA_DIR / f"debug_{tag}_{ts}.txt"
        f1.write_text(snap, encoding="utf-8")
        f2 = DATA_DIR / f"debug_{tag}_{ts}.png"
        await page.screenshot(path=str(f2))
        log(f"     现场已保存: {f1.name} / {f2.name} (URL: {page.url})")
    except Exception as e:
        log(f"     现场保存失败: {e}")


async def open_confirm_page(ctx, page, nid):
    """点立即购买，返回确认订单页（并发安全：只监听当前页面的弹窗）。
    兼容同 tab 跳转/新标签页/点击失败重试。返回渲染完成的确认页 page；失败 None。"""
    target = None
    for attempt in range(2):
        # 方式1: 监听当前页面的新窗口（expect_popup 只跟本页绑定，并发下不会串）
        try:
            async with page.expect_popup(timeout=10000) as popup_info:
                await click_buy_now(page)
            t = await popup_info.value
            if nid in t.url:
                target = t
                break
        except Exception:
            pass
        # 方式2: 当前页直接跳转
        if target is None:
            try:
                await page.wait_for_url(re.compile(r"buy\.|confirm", re.I), timeout=10000)
                if nid in page.url:
                    target = page
                    break
            except Exception:
                pass
        # 方式3: 再点一次
        if target is None:
            await asyncio.sleep(2)
    if target is None:
        return None
    # 等付款区域渲染完成（绝不点击）
    try:
        await target.wait_for_function("""() => {
            const t = (document.body.innerText || '').replace(/\\s+/g, '');
            return t.includes('合计') || t.includes('立即支付') || t.includes('提交订单');
        }""", timeout=20000)
    except Exception:
        await _snapshot(target, f"timeout_{nid}")
        return None
    await asyncio.sleep(random.uniform(1.5, 2.5))
    return target


async def _verify_candidate(ctx, cand, it, spec, pack, packs):
    """单个候选完整验证（独立标签页，可并发）。返回 result dict 或 None"""
    page = await ctx.new_page()
    try:
        await page.goto(cand["url"], wait_until="domcontentloaded", timeout=60000)
        await human_delay(1.5, 2.5)
        qty = packs
        per_pack = None
        total_count = it.get("total")
        if total_count is None and pack:
            m_total = re.search(r"(\d+)\s*个", pack)
            if m_total:
                total_count = int(m_total.group(1)) * packs
        if spec:
            sel_text, per_pack, qty = await pick_sku(page, spec, pack, total_count)
            if not sel_text:
                log(f"     {cand['shop']}: 无匹配规格，跳过")
                return None
            if per_pack and total_count:
                log(f"     {cand['shop']}: 总需求{total_count}个 ÷ SKU每包{per_pack}个 = {qty}件")
            elif total_count:
                log(f"     {cand['shop']}: SKU无包数，按单件计 {qty} 件")
        elif pack:
            await _click_sku_by_text(page, pack)
        if qty > 1:
            await set_qty(page, qty)
        target = await open_confirm_page(ctx, page, cand["nid"])
        if target is None:
            log(f"     {cand['shop']}: 未能进入确认页，跳过")
            return None
        body_text = await target.inner_text("body")
        norm = re.sub(r"\s+", "", body_text)
        pay_region = norm[norm.find("付款详情"):] if "付款详情" in norm else norm
        total = _find_amount(pay_region, "商品总价")
        pay = _find_amount(pay_region, "立即支付") or _find_amount(pay_region, "合计")
        saved = _find_amount(pay_region, "优惠共减") or _find_amount(pay_region, "店铺优惠")
        total_price = None
        if per_pack and total_count and pay and qty:
            unit_per_pack = pay / qty
            packs_needed = math.ceil(total_count / per_pack)
            total_price = round(packs_needed * unit_per_pack, 2)
        elif per_pack is None and total_count and pay:
            total_price = round(pay, 2)
        result = {
            "nid": cand["nid"], "shop": cand["shop"], "title": cand["title"],
            "qty": qty, "per_pack": per_pack, "total_count": total_count,
            "total": total, "pay": pay, "saved": saved,
            "unit_pay": round(pay / qty, 4) if pay else None,
            "total_price": total_price, "url": cand["url"],
        }
        log(f"     ✅ {cand['shop']} 实付 {pay} (原{total} 省{saved})" +
            (f" | 买齐需约 {total_price}" if total_price else " | 件数口径未知"))
        return result
    except Exception as e:
        log(f"     {cand['shop']} 出错: {e}")
        return None
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def cmd_verify(pw, nid, sku_text, qty):
    """核心：选规格→设数量→立即购买→确认订单页读实付（不提交订单）

    实付金额是淘宝按账号算好的最终价（含店铺券/满减等）。
    安全红线：绝不点击"提交订单"，只读价格。
    """
    browser, ctx, page = await launch(pw)
    try:
        ok, nick = await is_logged_in(page)
        if not ok:
            log("未登录，先运行: python taobao_bot.py session")
            return 2
        url = f"https://item.taobao.com/item.htm?id={nid}"
        log(f"[1/4] 打开详情页: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await human_delay(3.0, 5.0)

        if sku_text:
            log(f"[2/4] 选择规格: {sku_text}")
            try:
                sku = page.locator("[class*='valueItem']", has_text=sku_text).first
                await sku.click(timeout=8000)
            except Exception:
                log("  SKU 选择器失败，改用文本点击")
                await page.get_by_text(sku_text, exact=False).first.click(timeout=5000)
            await human_delay(1.5, 2.5)

        if qty and qty > 1:
            log(f"[2.5] 设置数量: {qty}")
            await set_qty(page, qty)

        log("[3/4] 点击 立即购买 并等待确认页")
        target = await open_confirm_page(ctx, page, nid)
        if target is None:
            log("  未能进入确认页（跳转失败或商品不符）")
            return 3
        log("  确认页渲染完成（付款区域可见，不会点击支付）")

        for _ in range(2):
            await target.mouse.wheel(0, 800)
            await human_delay(0.8, 1.5)

        # 读渲染后的正文文本，去掉空白归一化（页面会把价格拆成多个 span）
        body_text = await target.inner_text("body")
        norm = re.sub(r"\s+", "", body_text)
        # 只取"付款详情"之后的区域，避免推荐位干扰
        pay_region = norm[norm.find("付款详情"):] if "付款详情" in norm else norm
        ts = time.strftime("%Y%m%d_%H%M%S")
        tfile = DATA_DIR / f"confirm_{nid}_{ts}.txt"
        tfile.write_text(body_text, encoding="utf-8")
        log(f"确认页正文已保存: {tfile}  (URL: {target.url})")

        total = _find_amount(pay_region, "商品总价")
        pay = _find_amount(pay_region, "立即支付")
        if pay is None:
            pay = _find_amount(pay_region, "合计")
        saved = _find_amount(pay_region, "优惠共减")
        if saved is None:
            saved = _find_amount(pay_region, "店铺优惠")
        discount = round((total - pay), 2) if (total is not None and pay is not None) else None
        result = {
            "nid": nid,
            "sku": sku_text,
            "qty": qty,
            "total": total,
            "pay": pay,
            "discount": discount,
            "saved": saved,
            "freight": _find_amount(pay_region, "运费"),
            "url": target.url,
        }
        log(f"  📦 结果: {json.dumps(result, ensure_ascii=False)}")
        if pay is not None and total is not None and pay < total:
            log(f"  💰 有优惠: 总价{total} → 实付{pay}，省了{discount}")
        elif pay is not None:
            log("  💰 无优惠（总价=实付）")
        log("⚠️ 未点击支付，仅读取")
        return 0
    finally:
        await browser.close()


def _find_amount(norm_text: str, key: str):
    """在去空白文本中找 '关键字￥xx.xx' 的金额（兼容全角￥/半角¥）"""
    m = re.search(re.escape(key) + r"(?:¥|￥)?(\d+(?:\.\d+)?)", norm_text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


async def cmd_compare(pw, listfile, top, out_path="", concurrency=3):
    """批量比价：清单 → 逐项搜索+候选verify → 排序输出

    listfile 格式: [{"name":"304不锈钢外六角螺栓","spec":"M8*30","pack":"100个","packs":3}, ...]
    - name: 品名（搜索词）  spec: 规格匹配  pack: SKU包装（优先匹配）  packs: 件数
    """
    items = json.loads(Path(listfile).read_text(encoding="utf-8"))
    browser, ctx, page = await launch(pw)
    try:
        ok, nick = await is_logged_in(page)
        if not ok:
            log("未登录，先运行: python taobao_bot.py session")
            return 2
        log(f"登录用户: {nick}，清单 {len(items)} 项，每项取前 {top} 个候选")
        all_results = []
        for idx, it in enumerate(items):
            name = it.get("name", "")
            spec = it.get("spec", "")
            pack = it.get("pack", "")
            packs = it.get("packs", 1)
            log(f"\n===== [{idx+1}/{len(items)}] {name} {spec} x{packs} =====")
            kw = f"{name} {spec}".strip()
            await page.goto("https://s.taobao.com/search?q=" + urllib.parse.quote(kw),
                            wait_until="domcontentloaded", timeout=60000)
            await human_delay(2.0, 4.0)
            for _ in range(2):
                await page.mouse.wheel(0, 1200)
                await human_delay(1.0, 2.0)
            html = await page.content()
            cands = parse_search_html(html, top + 3)  # 多取 3 家候选，供异常排除后补位
            if not cands:
                # 搜索偶发被风控拦截（0 候选）时重试一次
                log("  搜索无结果，等待后重试一次…")
                await human_delay(5.0, 8.0)
                await page.goto("https://s.taobao.com/search?q=" + urllib.parse.quote(kw),
                                wait_until="domcontentloaded", timeout=60000)
                await human_delay(3.0, 5.0)
                for _ in range(2):
                    await page.mouse.wheel(0, 1200)
                    await human_delay(1.0, 2.0)
                html = await page.content()
                cands = parse_search_html(html, top + 3)
            # 两级筛选：按搜索页价格+销量粗排，只对最低价的候选做深度验证
            def _price_key(c):
                try:
                    return float(c.get("price") or 0)
                except ValueError:
                    return 1e18
            cands.sort(key=lambda c: (_price_key(c),
                                      -(int(re.sub(r"\D", "", c.get("sales") or "0") or 0))))
            log(f"  候选 {len(cands)} 个（按搜索价粗排，并发验证，异常时补跑备选）")
            # 并发验证：每批 concurrency 个候选（同一账号并发需控制，防风控）
            item_results = []
            sem = asyncio.Semaphore(concurrency)
            for batch_start in range(0, len(cands), concurrency):
                if len(item_results) >= top:
                    break
                batch = cands[batch_start:batch_start + concurrency]
                for ci, cand in enumerate(batch):
                    log(f"  -- 候选{batch_start+ci+1}: {cand['shop']} ¥{cand['price']} | {cand['title'][:34]}")

                async def _run(cand):
                    async with sem:
                        return await _verify_candidate(ctx, cand, it, spec, pack, packs)

                rs = await asyncio.gather(*[_run(c) for c in batch])
                for r in rs:
                    if r:
                        item_results.append(r)
            # 异常价格过滤：与中位数偏离超过 4 倍的候选排除（可能是选错规格/口径错误）
            prices = [r.get("total_price") or r.get("pay") for r in item_results
                      if (r.get("total_price") or r.get("pay"))]
            if len(prices) >= 3:
                med = statistics.median(prices)
                if med > 0:
                    kept = []
                    for r in item_results:
                        p = r.get("total_price") or r.get("pay")
                        if p is None or (med / 4 <= p <= med * 4):
                            kept.append(r)
                        else:
                            log(f"     排除异常价格: {r['shop']} ¥{p}（中位数 ¥{med:.2f}，偏离过大）")
                    item_results = kept
            def _sort_key(r):
                v = r.get("total_price")
                if v is None:
                    v = r.get("pay")
                return (v is None, v or 1e18)
            item_results.sort(key=_sort_key)
            all_results.append({"item": it, "results": item_results[:top]})

        out = Path(out_path) if out_path else DATA_DIR / f"compare_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"\n结果已保存: {out}")
        for g in all_results:
            it = g["item"]
            log(f"\n=== {it.get('name')} {it.get('spec','')} (需 {it.get('packs',1)} 件) ===")
            for r in g["results"]:
                log(f"  {r['shop']} | 买齐需 {r['total_price']} | 实付{r['pay']} (原{r['total']}, 省{r['saved']}) | {r['url']}")
        return 0
    finally:
        await browser.close()


async def cmd_dom(pw, nid):
    """打开详情页，探测页面上 SKU/价格/按钮等交互元素（调试用）"""
    browser, ctx, page = await launch(pw)
    try:
        ok, nick = await is_logged_in(page)
        if not ok:
            log("未登录")
            return 2
        url = f"https://item.taobao.com/item.htm?id={nid}"
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await human_delay(3.0, 5.0)
        # 尝试点开 SKU 面板（新版页面需点"选择规格"之类）
        for kw in ["选择", "规格", "立即购买", "加入购物车"]:
            try:
                btn = page.get_by_text(kw, exact=False).first
                if await btn.count():
                    await btn.click(timeout=3000)
                    await human_delay(1.0, 2.0)
                    break
            except Exception:
                pass
        # 收集含关键 class 的元素
        seen = set()
        for sel in ["[class*='sku' i]", "[class*='Sku']", "[class*='price' i]",
                    "[class*='Price']", "[class*='button' i]", "[class*='Button']",
                    "[class*='quantity' i]", "button", "li", "a"]:
            try:
                els = page.locator(sel)
                n = await els.count()
                for i in range(min(n, 60)):
                    try:
                        cls = await els.nth(i).get_attribute("class") or ""
                        txt = (await els.nth(i).inner_text(timeout=1500) or "").strip().replace("\n", " ")
                        tag = await els.nth(i).evaluate("el => el.tagName")
                        key = f"{tag}|{cls[:50]}|{txt[:40]}"
                        if key in seen:
                            continue
                        seen.add(key)
                        print(f"[{tag}] class={cls[:70]!r} text={txt[:60]!r}")
                    except Exception:
                        pass
            except Exception:
                pass
        return 0
    finally:
        await browser.close()


async def cmd_detail(pw, nid):
    """打开商品详情页，检测券后价/SKU 结构，dump HTML 供分析"""
    browser, ctx, page = await launch(pw)
    try:
        ok, nick = await is_logged_in(page)
        if not ok:
            log("未登录，先运行: python taobao_bot.py session")
            return 2
        url = f"https://item.taobao.com/item.htm?id={nid}"
        log(f"打开详情页: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await human_delay(2.0, 3.5)
        for _ in range(2):
            await page.mouse.wheel(0, 1000)
            await human_delay(0.8, 1.5)
        html = await page.content()
        ts = time.strftime("%Y%m%d_%H%M%S")
        hfile = DATA_DIR / f"detail_{nid}_{ts}.html"
        hfile.write_text(html, encoding="utf-8")
        log(f"HTML 已保存: {hfile}")
        for kw in ["券后", "优惠券", "领券", "满", "sku", "规格", "库存", "总价"]:
            n = len(re.findall(re.escape(kw), html))
            log(f"  特征[{kw}]: {n}")
        return 0
    finally:
        await browser.close()


async def cmd_parse(pw, html_path):
    html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    items = parse_search_html(html, 100)
    print(json.dumps(items, ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="淘宝自动比价助手")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("session", help="登录并保存会话")
    sub.add_parser("check", help="检查登录态")
    p = sub.add_parser("search", help="搜索商品")
    p.add_argument("keyword")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--dump", action="store_true", help="仅保存HTML不解析")
    p = sub.add_parser("detail", help="打开商品详情页并分析")
    p.add_argument("nid")
    p = sub.add_parser("dom", help="探测详情页交互元素（调试）")
    p.add_argument("nid")
    p = sub.add_parser("verify", help="选规格→立即购买→读实付（不提交订单）")
    p.add_argument("nid")
    p.add_argument("sku_text", nargs="?", default="", help="规格文本，如 M8*30；无规格商品留空")
    p.add_argument("--qty", type=int, default=1, help="数量")
    p = sub.add_parser("compare", help="清单批量比价")
    p.add_argument("listfile", help="清单 JSON 文件路径")
    p.add_argument("--top", type=int, default=5, help="每项取前 N 个候选")
    p.add_argument("--out", default="", help="结果输出文件路径（默认 data/compare_时间戳.json）")
    p.add_argument("--concurrency", type=int, default=3,
                   help="并发验证的标签页数（默认3，同一账号并发过高易触发风控）")
    p = sub.add_parser("parse", help="解析已保存的HTML")
    p.add_argument("html_path")
    args = ap.parse_args()

    async def run():
        async with async_playwright() as pw:
            if args.cmd == "session":
                return await cmd_session(pw)
            if args.cmd == "check":
                return await cmd_check(pw)
            if args.cmd == "search":
                return await cmd_search(pw, args.keyword, args.limit, args.dump)
            if args.cmd == "detail":
                return await cmd_detail(pw, args.nid)
            if args.cmd == "dom":
                return await cmd_dom(pw, args.nid)
            if args.cmd == "verify":
                return await cmd_verify(pw, args.nid, args.sku_text, args.qty)
            if args.cmd == "compare":
                return await cmd_compare(pw, args.listfile, args.top, args.out, args.concurrency)
            if args.cmd == "parse":
                return await cmd_parse(pw, args.html_path)
            return 1

    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
