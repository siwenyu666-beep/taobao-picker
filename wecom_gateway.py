#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
企业微信网关：微信/企微里发购物清单 → 自动比价 → 回发排序结果

流程：
  企微应用消息(清单文本) → 本网关(flask) → 解析清单 → 调 compare → 格式化 → 发回企微

运行前：
  1. 在 config.json 填 corpid / agentid / secret / token / encodingaeskey
  2. python wecom_gateway.py   （监听 127.0.0.1:8899）
  3. cloudflared tunnel --url http://127.0.0.1:8899   （内网穿透）
  4. 企微后台"接收消息"回调 URL = https://<隧道域名>/wecom

安全：只响应企微回调路径；清单解析失败会回发提示；绝不自动下单。
"""
import base64
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import flask
from Crypto.Cipher import AES

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
BOT = BASE_DIR / "taobao_bot.py"
PY = BASE_DIR / ".venv" / "Scripts" / "python.exe"
PORT = 8899

app = flask.Flask(__name__)


# ---------- 企微加解密（官方 WXBizMsgCrypt 算法） ----------
class WXBizMsgCrypt:
    def __init__(self, token, encoding_aes_key, corp_id):
        self.key = base64.b64decode(encoding_aes_key + "=")
        assert len(self.key) == 32
        self.token = token
        self.corp_id = corp_id

    def _signature(self, timestamp, nonce, encrypt):
        arr = sorted([self.token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(arr).encode()).hexdigest()

    def _decrypt(self, encrypt):
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        raw = cipher.decrypt(base64.b64decode(encrypt))
        pad = raw[-1]
        raw = raw[:-pad]
        msg_len = struct.unpack(">I", raw[16:20])[0]
        msg = raw[20:20 + msg_len].decode("utf-8")
        receiveid = raw[20 + msg_len:].decode("utf-8")
        return msg, receiveid

    def _encrypt(self, msg):
        raw = (os.urandom(16)
               + struct.pack(">I", len(msg.encode()))
               + msg.encode()
               + self.corp_id.encode())
        pad = 32 - len(raw) % 32
        raw += bytes([pad]) * pad
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        return base64.b64encode(cipher.encrypt(raw)).decode()

    def verify_url(self, msg_signature, timestamp, nonce, echostr):
        if self._signature(timestamp, nonce, echostr) != msg_signature:
            return None
        msg, _ = self._decrypt(echostr)
        return msg

    def decrypt_msg(self, msg_signature, timestamp, nonce, encrypt):
        if self._signature(timestamp, nonce, encrypt) != msg_signature:
            return None, None
        return self._decrypt(encrypt)

    def encrypt_reply(self, reply):
        return self._encrypt(reply)


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


CFG = load_config()
CRYPT = WXBizMsgCrypt(CFG.get("token", ""), CFG.get("encodingaeskey", ""), CFG.get("corpid", ""))


JOBS = {}
HISTORY_FILE = BASE_DIR / "data" / "chat_history.json"
COMPARE_LOCK = threading.Lock()  # 同时只跑一个比价（防并发操作淘宝账号）

CHAT_HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>五金采购助手</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#1b6ef3">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="五金助手">
<link rel="apple-touch-icon" href="/icon-192.png">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Microsoft YaHei',sans-serif;background:#eef1f5;height:100dvh;display:flex;flex-direction:column;overflow:hidden}
header{background:#1b6ef3;color:#fff;padding:12px 16px;font-size:17px;font-weight:bold;flex-shrink:0;z-index:9}
#msgs{flex:1;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px;-webkit-overflow-scrolling:touch}
.msg{max-width:85%;padding:9px 12px;border-radius:12px;font-size:15px;line-height:1.55;word-break:break-all;white-space:pre-wrap}
.me{background:#95ec69;align-self:flex-end;border-bottom-right-radius:3px}
.ai{background:#fff;align-self:flex-start;border-bottom-left-radius:3px;box-shadow:0 1px 2px rgba(0,0,0,.08);white-space:normal;max-width:92%}
.ai table{border-collapse:collapse;margin-top:6px;width:100%}
.ai th,.ai td{padding:5px 6px;border:1px solid #e3e8ef;font-size:13px;text-align:left}
.ai th{background:#f0f5ff;color:#1b6ef3}
.ai a{color:#1b6ef3}
.best td{background:#e8f3ff;font-weight:bold}
.warn{color:#e67e22;font-size:12px}
.hint{color:#aaa;font-size:12px}
#inputArea{background:#f7f8fa;border-top:1px solid #ddd;padding:8px 10px 10px;flex-shrink:0}
#rows{margin-bottom:6px}
.row{display:flex;gap:4px;margin-bottom:6px;align-items:center}
.row input{border:1px solid #ccc;border-radius:6px;padding:8px 4px;font-size:15px;text-align:center;min-width:0;height:38px}
.c-name{flex:2.2}.c-spec{flex:1.8}.c-count{flex:1}
.del{flex:0 0 28px;height:38px;border:none;background:#ffecec;color:#e33;border-radius:6px;font-size:15px}
.rowhead{display:flex;gap:4px;margin-bottom:4px;font-size:11px;color:#999}
.rowhead div{flex:1;text-align:center}.rowhead .c-name{flex:2.2}.rowhead .c-spec{flex:1.8}.rowhead .c-count{flex:1}.rowhead .del{flex:0 0 28px}
.btnrow{display:flex;gap:8px}
#addRow{flex:1;padding:9px;border:1px dashed #1b6ef3;background:#f4f8ff;color:#1b6ef3;border-radius:8px;font-size:14px}
#sendBtn{flex:2;padding:10px;background:#1b6ef3;color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:bold}
</style></head><body>
<header>🔧 五金采购助手</header>
<div id="msgs"></div>
<div id="inputArea">
  <div class="rowhead"><div class="c-name">品名</div><div class="c-spec">规格</div><div class="c-count">个数</div><div class="del"></div></div>
  <div id="rows"></div>
  <div class="btnrow"><button id="addRow" type="button">＋ 添加一行</button><button id="sendBtn" type="button">发送比价</button></div>
</div>
<script>
var msgs=document.getElementById('msgs'),rows=document.getElementById('rows');
function addMsg(cls,html){var d=document.createElement('div');d.className='msg '+cls;d.innerHTML=html;msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d;}
function esc(s){return String(s).replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function addRow(){
  var d=document.createElement('div');d.className='row';
  d.innerHTML="<input class='c-name' placeholder='品名'><input class='c-spec' placeholder='如 M8×30 或 M8'><input class='c-count' placeholder='总数'><button class='del' type='button'>✕</button>";
  d.querySelector('.del').onclick=function(){d.remove();};
  rows.appendChild(d);
}
function bind(id,fn){var el=document.getElementById(id);if(!el)return;
  el.addEventListener('click',fn);
  el.addEventListener('touchend',function(e){e.preventDefault();fn();});
}
function collect(){
  var lines=[];
  var rs=rows.querySelectorAll('.row');
  for(var i=0;i<rs.length;i++){
    var r=rs[i];
    var name=(r.querySelector('.c-name').value||'').trim();
    var spec=(r.querySelector('.c-spec').value||'').trim();
    var count=(r.querySelector('.c-count').value||'').trim();
    if(!name&&!spec&&!count)continue;
    if(!name)name=spec;
    var line=name+(spec&&spec!==name?' '+spec:'');
    if(count)line+='，'+count+'个';
    lines.push(line);
  }
  return lines.join('\\n');
}
function loadHistory(){
  addMsg('ai','你好！我是你的五金采购助手 🤝<span class="hint"> v6</span><br>在下方表格填：品名 / 规格（如 M8×30）/ 个数，点「发送比价」即可。<br><span class="warn">微信里建议右上角「···」→「在浏览器打开」最顺畅</span>');
  fetch('/chat/history').then(function(r){return r.json()}).then(function(h){
    if(!h||!h.length)return;
    h.forEach(function(rec){
      addMsg('me',esc(rec.text||''));
      if(rec.status==='done'&&rec.html)addMsg('ai',rec.html+'<br><span class="hint">'+rec.time+'</span>');
      else addMsg('ai','❌ '+(rec.error||'失败')+'<br><span class="hint">'+rec.time+'</span>');
    });
  }).catch(function(){});
}
function send(){
  var text=collect();
  if(!text){addMsg('ai','⚠️ 请至少填写一行');return;}
  addMsg('me',text);
  var pend=addMsg('ai','⏳ 正在逐店比价（约2-4分钟）…');
  fetch('/chat/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text})})
  .then(function(r){return r.json()}).then(function(j){
    if(!j.ok){pend.remove();addMsg('ai','⚠️ '+j.msg);return;}
    var iv=setInterval(function(){
      fetch('/chat/status/'+j.id).then(function(r){return r.json()}).then(function(s){
        if(s.status==='running')return;
        clearInterval(iv);pend.remove();
        if(s.status==='done')addMsg('ai',s.html);
        else addMsg('ai','❌ '+s.msg);
      }).catch(function(){});
    },8000);
  }).catch(function(){pend.remove();addMsg('ai','⚠️ 发送失败，请重试');});
  rows.innerHTML='';addRow();
}
addRow();
bind('addRow',addRow);
bind('sendBtn',send);
loadHistory();
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){});}
</script></body></html>"""


@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/manifest.json")
def pwa_manifest():
    return flask.Response(json.dumps({
        "name": "五金采购助手",
        "short_name": "五金助手",
        "description": "淘宝自动比价：发清单→真实券后价排序",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#eef1f5",
        "theme_color": "#1b6ef3",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/icon-192.png")
def pwa_icon192():
    return flask.send_file(BASE_DIR / "assets" / "icon-192.png", mimetype="image/png")


@app.route("/icon-512.png")
def pwa_icon512():
    return flask.send_file(BASE_DIR / "assets" / "icon-512.png", mimetype="image/png")


@app.route("/sw.js")
def pwa_sw():
    return flask.Response(
        "self.addEventListener('install',e=>self.skipWaiting());"
        "self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));"
        "self.addEventListener('fetch',e=>{e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));});",
        mimetype="application/javascript")


@app.route("/", methods=["GET"])
def index():
    return CHAT_HTML


@app.route("/chat/send", methods=["POST"])
def chat_send():
    try:
        text = flask.request.get_json(force=True).get("text", "")
    except Exception:
        return flask.jsonify(ok=False, msg="消息格式错误")
    items = parse_list(text)
    if not items:
        return flask.jsonify(ok=False, msg="清单没看懂，发\"帮助\"看格式")
    jid = f"{int(time.time() * 1000)}"
    JOBS[jid] = {"status": "running", "items": items, "text": text}
    threading.Thread(target=run_job, args=(jid, items), daemon=True).start()
    return flask.jsonify(ok=True, id=jid)


@app.route("/chat/history")
def chat_history():
    """返回本次开机后（网关进程）的全部聊天记录"""
    if HISTORY_FILE.exists():
        try:
            return flask.Response(
                HISTORY_FILE.read_text(encoding="utf-8"), mimetype="application/json")
        except Exception:
            pass
    return flask.jsonify([])


@app.route("/chat/status/<jid>")
def chat_status(jid):
    job = JOBS.get(jid)
    if not job:
        return flask.jsonify(status="error", msg="任务不存在")
    if job["status"] == "running":
        return flask.jsonify(status="running")
    if job["status"] == "error":
        return flask.jsonify(status="error", msg=job.get("error", "比价失败"))
    return flask.jsonify(status="done", html=format_result_fragment(job["result"]))


def format_result_fragment(compare_json):
    """比价结果 → 聊天气泡 HTML 片段（表格）"""
    html = []
    for g in compare_json:
        it = g["item"]
        if it.get("total"):
            need = f"共 {it['total']} 个"
        elif it.get("pack"):
            need = f"{it.get('pack')} × {it.get('packs',1)}"
        else:
            need = f"{it.get('packs',1)}件"
        html.append(f"<b>🔩 {it['name']} {it.get('spec','')}</b><br><span class='warn'>需 {need}</span>")
        if not g["results"]:
            html.append("<br>暂无有效比价结果")
            continue
        html.append("<table><tr><th>#</th><th>店铺</th><th>包装</th><th>件数</th><th>买齐价</th><th>省</th><th></th></tr>")
        for i, r in enumerate(g["results"], 1):
            tp = f"¥{r['total_price']}" if r.get("total_price") else f"¥{r.get('pay')}"
            saved = f"-¥{r['saved']}" if r.get("saved") else "—"
            cls = " class='best'" if i == 1 else ""
            url = r.get("url", "")
            scheme = url.replace("https://", "taobao://") if url else "#"
            pack_info = f"{r['per_pack']}个/包" if r.get("per_pack") else "单件"
            qty_info = f"{r['qty']}件" if r.get("qty") else "—"
            html.append(f"<tr{cls}><td>{i}</td><td>{r['shop']}</td><td>{pack_info}</td><td>{qty_info}</td>"
                        f"<td><b>{tp}</b></td><td>{saved}</td>"
                        f"<td><a href='{scheme}'>App</a> <a href='{url}' target='_blank'>网页</a></td></tr>")
        html.append("</table>")
    html.append("<br><span class='warn'>比价只读，未下单；点\"淘宝\"链接到淘宝确认后手动支付</span>")
    return "".join(html)


def run_job(jid, items):
    job = JOBS.get(jid, {})
    try:
        proc, res = run_compare(items, int(CFG.get("top", 5)))
        job["status"] = "done" if res else "error"
        job["result"] = res
        job["error"] = None if res else f"compare 退出码 {proc.returncode}"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    # 写入历史（本次开机后所有记录）
    try:
        rec = {
            "time": time.strftime("%m-%d %H:%M"),
            "text": job.get("text", ""),
            "status": job["status"],
            "html": format_result_fragment(job["result"]) if job.get("result") else None,
            "error": job.get("error"),
        }
        hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else []
        hist.append(rec)
        HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[gateway] 历史保存失败: {e}", flush=True)


def format_result_html(compare_json):
    html = ['<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<title>比价结果</title><style>',
            'body{font-family:-apple-system,"Microsoft YaHei",sans-serif;max-width:680px;margin:0 auto;padding:16px;background:#f5f7fa}',
            'h2{font-size:17px;color:#1b6ef3;margin-top:20px}',
            'table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;font-size:14px}',
            'th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #eee}',
            'th{background:#1b6ef3;color:#fff;font-size:13px}',
            '.best{background:#e8f3ff;font-weight:bold}',
            '.warn{color:#e67e22;font-size:12px}',
            'a{color:#1b6ef3;text-decoration:none}',
            '</style></head><body>',
            '<h1 style="font-size:20px;color:#1b6ef3">📊 比价结果（券后实付）</h1>',
            '<p style="color:#888;font-size:12px">比价只读，未下任何订单；点链接到淘宝确认后手动支付</p>']
    for g in compare_json:
        it = g["item"]
        need = f"{it.get('pack','单件')} × {it.get('packs',1)}"
        html.append(f'<h2>🔩 {it["name"]} {it.get("spec","")} <span style="color:#888;font-size:13px">（需 {need}）</span></h2>')
        if not g["results"]:
            html.append("<p>暂无有效比价结果</p>")
            continue
        html.append("<table><tr><th>#</th><th>店铺</th><th>买齐总价</th><th>优惠</th><th>每件</th><th>链接</th></tr>")
        for i, r in enumerate(g["results"], 1):
            tp = f"¥{r['total_price']}" if r.get("total_price") else f"¥{r.get('pay')}"
            saved = f"-¥{r['saved']}" if r.get("saved") else "—"
            unit = f"¥{r['unit_pay']}" if r.get("unit_pay") else "—"
            cls = " class='best'" if i == 1 else ""
            html.append(
                f"<tr{cls}><td>{i}</td><td>{r['shop']}</td><td><b>{tp}</b></td>"
                f"<td>{saved}</td><td>{unit}</td>"
                f"<td><a href='{r['url']}' target='_blank'>查看</a></td></tr>")
        html.append("</table>")
    html.append('<p style="color:#888;font-size:12px;margin-top:20px"><a href="/">← 再比一次</a></p></body></html>')
    return "".join(html)


@app.route("/wecom", methods=["GET", "POST"])
def wecom_callback():
    if not CFG.get("token"):
        return "config missing", 500
    msg_signature = flask.request.args.get("msg_signature", "")
    timestamp = flask.request.args.get("timestamp", "")
    nonce = flask.request.args.get("nonce", "")
    if flask.request.method == "GET":
        echostr = flask.request.args.get("echostr", "")
        got = CRYPT._signature(timestamp, nonce, echostr)
        print(f"[gateway] GET verify: sig_ok={got == msg_signature} echostr_len={len(echostr)}", flush=True)
        plain = CRYPT.verify_url(msg_signature, timestamp, nonce, echostr)
        if plain is not None:
            return plain
        return "verify failed", 403
    # POST: 消息回调
    try:
        data = flask.request.get_data(as_text=True)
        encrypt = re.search(r"<Encrypt><!\[CDATA\[(.*?)\]\]></Encrypt>", data).group(1)
        msg, _ = CRYPT.decrypt_msg(msg_signature, timestamp, nonce, encrypt)
        if msg is None:
            return "signature fail", 403
        from_user = re.search(r"<FromUserName><!\[CDATA\[(.*?)\]\]></FromUserName>", msg)
        content = re.search(r"<Content><!\[CDATA\[(.*?)\]\]></Content>", msg)
        if from_user and content:
            threading.Thread(
                target=handle_message, args=(from_user.group(1), content.group(1)), daemon=True
            ).start()
    except Exception as e:
        print(f"[gateway] 回调解析失败: {e}", flush=True)
    return "success"  # 企微要求立即回包


# ---------- 清单解析 ----------
def parse_list(text: str):
    """把自然语言清单解析成 compare 格式。支持多行、分号（；;）分隔多个需求。
    示例: '304不锈钢外六角螺栓 M8×30，100个/包，3包；304不锈钢平垫圈 M8，200个/包，2包'
    或 每行一项。返回 [{"name","spec","pack","packs"}, ...] 或 None"""
    items = []
    blocks = re.split(r"[\n;；]+", text)
    for raw in blocks:
        line = raw.strip().strip("，,。；; ")
        if not line:
            continue
        pack = None
        packs = 1
        total = None
        m_pack = re.search(r"(\d+(?:\.\d+)?)\s*个\s*/\s*包", line)
        if m_pack:
            pack = f"{int(float(m_pack.group(1)))}个"
        m_packs = re.search(r"[，,]\s*(\d+)\s*包\s*$", line)
        if m_packs:
            packs = int(m_packs.group(1))
        else:
            m_packs2 = re.search(r"(\d+)\s*包\s*$", line)
            if m_packs2:
                packs = int(m_packs2.group(1))
        # 总个数：如"品名 M8×30，30个"（无"个/包"时，"30个"=总需求数）
        if not m_pack:
            m_total = re.search(r"[，,]\s*(\d+)\s*个\s*$", line)
            if m_total:
                total = int(m_total.group(1))
        spec = ""
        m_spec = re.search(r"M\d+(?:\.\d+)?(?:[×xX*]\d+(?:\.\d+)?(?:[×xX*]\d+)?)?", line)
        if m_spec:
            spec = m_spec.group(0).replace("×", "*").replace("x", "*").replace("X", "*")
        name = line
        if m_pack:
            name = name.replace(m_pack.group(0), "")
        if m_packs:
            name = name.replace(m_packs.group(0), "")
        elif m_packs2:
            name = name.replace(m_packs2.group(0), "")
        if not m_pack and total is not None:
            m_total_src = re.search(r"[，,]\s*\d+\s*个\s*$", name)
            if m_total_src:
                name = name[:m_total_src.start()].rstrip("，, ")
        if m_spec:
            name = name.replace(m_spec.group(0), "").strip("，, ")
        name = name.strip("，, ")
        if not name:
            continue
        items.append({"name": name, "spec": spec, "pack": pack or "", "packs": packs, "total": total})
    return items or None


# ---------- 调 compare ----------
def run_compare(items, top):
    """串行调 compare，结果写入独立文件，避免并发串数据"""
    with COMPARE_LOCK:
        ts = int(time.time() * 1000)
        list_file = BASE_DIR / "data" / f"gw_list_{ts}.json"
        out_file = BASE_DIR / "data" / f"gw_out_{ts}.json"
        list_file.parent.mkdir(exist_ok=True)
        list_file.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            [str(PY), str(BOT), "compare", str(list_file), "--top", str(top),
             "--out", str(out_file), "--concurrency", str(int(CFG.get("concurrency", 3)))],
            capture_output=True, text=True, encoding="utf-8", timeout=2400,
        )
        result = None
        if out_file.exists():
            try:
                result = json.loads(out_file.read_text(encoding="utf-8"))
            except Exception:
                result = None
        return proc, result


def format_result(compare_json):
    """格式化成企微 markdown（企微不支持表格，用列表+链接）"""
    lines = ["📋 **比价结果（券后实付）**"]
    for g in compare_json:
        it = g["item"]
        need = it.get("pack", "") + "×" + str(it.get("packs", 1))
        lines.append(f"\n**{it['name']} {it.get('spec','')}**（需 {need}）")
        if not g["results"]:
            lines.append("> 暂无有效比价结果")
            continue
        for i, r in enumerate(g["results"], 1):
            total_price = r.get("total_price")
            tp = f"买齐 ¥{total_price}" if total_price else f"实付 ¥{r.get('pay')}"
            saved = f"（省{r.get('saved')}）" if r.get("saved") else ""
            pack_info = f"{r['per_pack']}个/包" if r.get("per_pack") else "单件"
            qty_info = f"{r['qty']}件" if r.get("qty") else ""
            lines.append(f"{i}. **{tp}** {saved} — {r['shop']}（{pack_info}{'×'+qty_info if qty_info else ''}）[查看商品]({r.get('url')})")
    lines.append("\n⚠️ 比价只读，未下任何订单；下单请点链接到淘宝确认后手动支付")
    return "\n".join(lines)


# ---------- 发送消息 ----------
def get_access_token():
    url = ("https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid="
           + CFG["corpid"] + "&corpsecret=" + CFG["secret"])
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return data.get("access_token")


def send_markdown(touser, content):
    token = get_access_token()
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    body = {
        "touser": touser,
        "msgtype": "markdown",
        "agentid": int(CFG["agentid"]),
        "markdown": {"content": content},
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def handle_message(from_user, content):
    try:
        print(f"[gateway] 收到 {from_user}: {content[:80]}", flush=True)
        items = parse_list(content)
        if not items:
            send_markdown(from_user, "⚠️ 清单没看懂，请按这个格式发：\n> 品名 规格，N个/包，M包\n例如：\n> 304不锈钢外六角螺栓 M8×30，100个/包，3包")
            return
        send_markdown(from_user, f"⏳ 收到 {len(items)} 项，正在逐店比价（约 {len(items)*3} 分钟），稍等…")
        proc, result = run_compare(items, int(CFG.get("top", 5)))
        if not result:
            send_markdown(from_user, f"❌ 比价失败（{proc.returncode}），请稍后重试")
            return
        send_markdown(from_user, format_result(result))
    except Exception as e:
        print(f"[gateway] 处理异常: {e}", flush=True)
        try:
            send_markdown(from_user, f"❌ 出错了：{e}")
        except Exception:
            pass


if __name__ == "__main__":
    if not CFG.get("corpid"):
        print("请先填写 config.json（corpid/agentid/secret/token/encodingaeskey）")
        raise SystemExit(1)
    print(f"[gateway] 监听 http://127.0.0.1:{PORT}/wecom ，等待企微回调…", flush=True)
    app.run(host="127.0.0.1", port=PORT, threaded=True)
