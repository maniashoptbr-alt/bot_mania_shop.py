import discord
from discord import app_commands
import threading
import os
import time
import json
import pyotp
import qrcode
from io import BytesIO
from flask import Flask
from datetime import datetime, timedelta

# --- CONFIGURAÇÕES ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
MEU_ID = 1516951987868925983
CANAL_CARRINHOS = 1516955638930870365
CANAL_PAGOS = 1516955638930870366
PIX_EMAIL = "maniashoptbr@gmail.com"
NOME_LOJA = "Mania Shop"

# --- SERVIDOR WEB PARA O RENDER ---
app = Flask(__name__)
@app.route('/')
def health_check(): return "Mania Shop Professional Online!", 200
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- PERSISTÊNCIA ---
def carregar_json(arq, default):
    if os.path.exists(arq):
        try:
            with open(arq, 'r', encoding='utf-8') as f: return json.load(f)
        except: return default
    return default

def salvar_json(arq, dados):
    with open(arq, 'w', encoding='utf-8') as f: json.dump(dados, f, indent=2, ensure_ascii=False)

produtos_disponiveis = carregar_json("produtos.json", {})
estoque_disponivel = carregar_json("estoque.json", {})

# --- LÓGICA PIX COPIA E COLA (PADRÃO OFICIAL) ---
def gerar_pix_br_code(valor, chave):
    def f(id, val): return f"{id}{len(str(val)):02}{val}"
    
    # Merchant Account Information (ID 26)
    gui = f("00", "BR.GOV.BCB.PIX")
    key = f("01", chave)
    merchant_info = f("26", gui + key)
    
    # Montagem do Payload
    payload = [
        f("00", "01"),          # Format Indicator
        merchant_info,          # Merchant Info
        f("52", "0000"),        # Category Code
        f("53", "986"),         # Currency (BRL)
        f("54", f"{valor:.2f}"),# Amount
        f("58", "BR"),          # Country
        f("59", "MANIA SHOP"),  # Name
        f("60", "SAO PAULO"),   # City
        f("62", f("05", "MANIA")), # Info Adicional
    ]
    
    pix_string = "".join(payload) + "6304"
    
    # Cálculo CRC16
    crc = 0xFFFF
    for char in pix_string:
        crc ^= (ord(char) << 8)
        for _ in range(8):
            if crc & 0x8000: crc = (crc << 1) ^ 0x1021
            else: crc <<= 1
    
    return pix_string + f"{crc & 0xFFFF:04X}"

# --- CONTROLE DE SPAM ---
cooldowns_pagamento = {}

# --- LÓGICA DE ESTOQUE ---
estoque_lock = threading.Lock()

def verificar_estoque(prod_id, var=None):
    with estoque_lock:
        if prod_id not in estoque_disponivel: return 0
        if var: return len(estoque_disponivel.get(prod_id, {}).get("variacoes", {}).get(var, []))
        return len(estoque_disponivel.get(prod_id, {}).get("itens", []))

def entregar_do_estoque(prod_id, var=None):
    with estoque_lock:
        if prod_id not in estoque_disponivel: return None
        lista = estoque_disponivel[prod_id].get("variacoes", {}).get(var, []) if var else estoque_disponivel[prod_id].get("itens", [])
        if lista:
            item = lista.pop(0)
            salvar_json("estoque.json", estoque_disponivel)
            return item
        return None

# --- DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class ManiaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
    async def setup_hook(self): await self.tree.sync()
    async def on_ready(self): print(f"🟢 {NOME_LOJA} logado como {self.user}")

bot = ManiaBot()

# --- VIEWS PROFISSIONAIS ---

class Modal2FA(discord.ui.Modal, title="Gerador de Código 2FA"):
    chave = discord.ui.TextInput(label="Cole sua chave 2FA aqui", placeholder="Ex: JBSWY3DPEHPK3PXP", required=True)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            totp = pyotp.TOTP(self.chave.value.strip().upper().replace(" ", ""))
            emb = discord.Embed(title="🔐 Autenticação 2FA", color=0x00ff88)
            emb.add_field(name="Código Atual", value=f"```\n{totp.now()}\n```", inline=False)
            emb.add_field(name="Expira em", value=f"{30 - (int(time.time()) % 30)} segundos")
            await interaction.response.send_message(embed=emb, ephemeral=True)
        except: await interaction.response.send_message("❌ Chave inválida!", ephemeral=True)

class Gerador2FAView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Gerar Código 2FA", style=discord.ButtonStyle.success, emoji="🔐")
    async def gerar(self, interaction: discord.Interaction, button: discord.ui.Button): await interaction.response.send_modal(Modal2FA())

class ConfirmarEntregaView(discord.ui.View):
    def __init__(self, cliente_id, prod_nome, pag_id, var=None):
        super().__init__(timeout=None)
        self.cliente_id, self.prod_nome, self.pag_id, self.var = cliente_id, prod_nome, pag_id, var

    @discord.ui.button(label="✅ Confirmar Entrega", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MEU_ID: return
        await interaction.response.defer()
        item = entregar_do_estoque(self.pag_id.split('_')[0], self.var)
        cliente = await bot.fetch_user(self.cliente_id)
        try:
            emb = discord.Embed(title="🎁 SEU PRODUTO CHEGOU!", color=0x00ff88)
            emb.add_field(name="📦 Produto", value=self.prod_nome)
            emb.add_field(name="🔐 Conteúdo", value=f"```{item if item else 'Entrega confirmada manualmente.'}```", inline=False)
            await cliente.send(embed=emb)
        except: pass
        canal = bot.get_channel(CANAL_PAGOS)
        if canal: await canal.send(embed=discord.Embed(title="✅ VENDA CONCLUÍDA", description=f"Cliente: <@{self.cliente_id}>\nProduto: {self.prod_nome}", color=0x00ff88))
        for child in self.children: child.disabled = True
        await interaction.edit_original_response(content="✅ Produto entregue!", view=self)

class ConfirmarPagamentoView(discord.ui.View):
    def __init__(self, prod_nome, valor, pag_id, copia_e_cola, var=None):
        super().__init__(timeout=None)
        self.prod_nome, self.valor, self.pag_id, self.copia_e_cola, self.var = prod_nome, valor, pag_id, copia_e_cola, var

    @discord.ui.button(label="📋 Copiar PIX", style=discord.ButtonStyle.secondary, emoji="📄")
    async def copiar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"```{self.copia_e_cola}```", ephemeral=True)

    @discord.ui.button(label="💰 Já realizei o pagamento", style=discord.ButtonStyle.primary, emoji="💸")
    async def confirmou(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        agora = datetime.now()
        if user_id in cooldowns_pagamento and agora < cooldowns_pagamento[user_id]:
            await interaction.response.send_message(f"⚠️ Aguarde um momento para confirmar novamente.", ephemeral=True)
            return
        cooldowns_pagamento[user_id] = agora + timedelta(minutes=2)
        await interaction.response.send_message("🚀 **Solicitação enviada!** Verificando seu pagamento.", ephemeral=True)
        canal = bot.get_channel(CANAL_CARRINHOS)
        if canal:
            emb = discord.Embed(title="🛒 NOVO PEDIDO", color=0xffaa00)
            emb.add_field(name="👤 Cliente", value=interaction.user.mention)
            emb.add_field(name="📦 Produto", value=self.prod_nome)
            emb.add_field(name="💰 Valor", value=f"R$ {self.valor:.2f}")
            await canal.send(content=f"<@{MEU_ID}>", embed=emb, view=ConfirmarEntregaView(interaction.user.id, self.prod_nome, self.pag_id, self.var))

async def fluxo_pagamento(interaction, prod_id, nome, preco, var=None):
    qtd = verificar_estoque(prod_id, var)
    if qtd <= 0:
        return await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("❌ **Sinto muito!** Este produto esgotou.", ephemeral=True)

    pag_id = f"{prod_id}_{interaction.user.id}_{int(time.time())}"
    pix_string = gerar_pix_br_code(preco, PIX_EMAIL)
    
    # Gerar QR Code Imagem
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(pix_string)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    
    emb = discord.Embed(title="💳 FINALIZAR PAGAMENTO", description=f"Produto: **{nome}**", color=0x8A05BE)
    emb.add_field(name="💰 Valor", value=f"```R$ {preco:.2f}```")
    emb.set_image(url="attachment://qrcode.png")
    emb.set_footer(text="Escaneie o QR Code ou use o botão 'Copiar PIX'.")
    
    view = ConfirmarPagamentoView(nome, preco, pag_id, pix_string, var)
    try:
        await interaction.user.send(file=discord.File(fp=buf, filename="qrcode.png"), embed=emb, view=view)
        await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("✅ **Pedido Gerado!** Verifique sua DM.", ephemeral=True)
    except: await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("❌ **Erro!** DM fechada.", ephemeral=True)

class ProdutoView(discord.ui.View):
    def __init__(self, prod_id, nome, vars=None):
        super().__init__(timeout=None)
        self.prod_id, self.nome, self.vars = prod_id, nome, vars or []
    @discord.ui.button(label="🛒 Adquirir Agora", style=discord.ButtonStyle.success, custom_id="btn_buy_pro")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = produtos_disponiveis.get(self.prod_id)
        if not p: return
        if self.vars:
            v = discord.ui.View()
            for var in self.vars:
                btn = discord.ui.Button(label=f"{var['nome']} - R$ {var['preco']:.2f}")
                async def cb(i, p_val=var['preco'], n_val=var['nome']): await fluxo_pagamento(i, self.prod_id, f"{self.nome} ({n_val})", p_val, n_val)
                btn.callback = cb
                v.add_item(btn)
            await interaction.response.send_message("✨ **Escolha sua opção:**", view=v, ephemeral=True)
        else: await fluxo_pagamento(interaction, self.prod_id, self.nome, p['preco'])

# --- COMANDOS ADMIN ---
@bot.tree.command(name="criar_produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str):
    if interaction.user.id != MEU_ID: return
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": descricao, "imagem": "", "variacoes": []}
    salvar_json("produtos.json", produtos_disponiveis); await interaction.response.send_message(f"✅ `{nome}` criado!", ephemeral=True)

@bot.tree.command(name="excluir_produto")
async def excluir_produto(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis:
        del produtos_disponiveis[id]; salvar_json("produtos.json", produtos_disponiveis)
        await interaction.response.send_message(f"🗑️ `{id}` excluído.", ephemeral=True)

@bot.tree.command(name="ver_indices")
async def ver_indices(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    p = produtos_disponiveis.get(id); e = estoque_disponivel.get(id, {})
    if not p: return await interaction.response.send_message("❌ ID não encontrado.", ephemeral=True)
    txt = f"📊 **GERENCIAMENTO: {p['nome']}**\n\n**🔹 VARIAÇÕES:**\n"
    for i, v in enumerate(p.get("variacoes", [])): txt += f"`{i}` - {v['nome']} (R$ {v['preco']:.2f})\n"
    txt += "\n**📦 ESTOQUE GERAL:**\n"
    for i, it in enumerate(e.get("itens", [])): txt += f"`{i}` - `{it}`\n"
    await interaction.response.send_message(txt[:2000], ephemeral=True)

@bot.tree.command(name="configurar_loja")
async def configurar_loja(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    p = produtos_disponiveis.get(id)
    if not p: return
    qtd = verificar_estoque(id)
    emb = discord.Embed(title=f"⚡ {p['nome']}", description=p['descricao'].replace("|", "\n"), color=0xffa500 if qtd > 0 else 0xff0000)
    emb.add_field(name="💰 Valor", value=f"R$ {p['preco']:.2f}", inline=True)
    emb.add_field(name="📦 Estoque", value=f"{qtd} un." if qtd > 0 else "ESGOTADO", inline=True)
    if p.get("imagem"): emb.set_image(url=p["imagem"])
    await interaction.channel.send(embed=emb, view=ProdutoView(id, p['nome'], p.get('variacoes')))
    await interaction.response.send_message("✅ OK!", ephemeral=True)

@bot.tree.command(name="add_estoque")
async def add_estoque(interaction: discord.Interaction, id: str, itens: str, variacao: str = None):
    if interaction.user.id != MEU_ID: return
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    if id not in estoque_disponivel: estoque_disponivel[id] = {"itens": [], "variacoes": {}}
    if variacao:
        if variacao not in estoque_disponivel[id]["variacoes"]: estoque_disponivel[id]["variacoes"][variacao] = []
        estoque_disponivel[id]["variacoes"][variacao].extend(novos)
    else: estoque_disponivel[id]["itens"].extend(novos)
    salvar_json("estoque.json", estoque_disponivel); await interaction.response.send_message(f"✅ +{len(novos)}", ephemeral=True)

@bot.tree.command(name="add_variacao")
async def add_variacao(interaction: discord.Interaction, id: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis:
        produtos_disponiveis[id]["variacoes"].append({"nome": nome, "preco": preco})
        salvar_json("produtos.json", produtos_disponiveis); await interaction.response.send_message("✅ OK!", ephemeral=True)

@bot.tree.command(name="set_imagem")
async def set_imagem(interaction: discord.Interaction, id: str, url: str):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis:
        produtos_disponiveis[id]["imagem"] = url; salvar_json("produtos.json", produtos_disponiveis)
        await interaction.response.send_message("✅ OK!", ephemeral=True)

@bot.tree.command(name="sincronizar")
async def sincronizar(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID: return
    await bot.tree.sync(); await interaction.response.send_message("✅ OK!", ephemeral=True)

@bot.tree.command(name="configurar_2fa")
async def configurar_2fa(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID: return
    emb = discord.Embed(title="🔐 GERADOR 2FA", description="Clique abaixo para gerar seu código.", color=0x00ff88)
    await interaction.channel.send(embed=emb, view=Gerador2FAView()); await interaction.response.send_message("✅ OK!", ephemeral=True)

# --- EXECUÇÃO ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    if DISCORD_TOKEN: bot.run(DISCORD_TOKEN)
