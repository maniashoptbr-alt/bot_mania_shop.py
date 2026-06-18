import discord
from discord import app_commands
import threading
import os
import time
import json
from datetime import datetime
import pyotp
from flask import Flask

# ===============================
# MINI SERVIDOR PARA O RENDER NÃO DAR FAILED
# ===============================
app = Flask('')

@app.route('/')
def home():
    return "Bot Mania Shop Online!"

def run():
    # O Render usa a porta 10000 por padrão
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

# ===============================
# CONFIG E BOT
# ===============================
print("🔧 Iniciando bot Mania Shop...")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"

MEU_ID = 1516951987868925983
CANAL_CARRINHOS = 1516955638930870365
CANAL_PAGOS = 1516955638930870366
PIX_EMAIL = "maniashoptbr@gmail.com"
NOME_LOJA = "Mania Shop"

# --- PERSISTÊNCIA ---
def carregar_json(arquivo, default):
    if os.path.exists(arquivo):
        try:
            with open(arquivo, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return default
    return default

def salvar_json(arquivo, dados):
    with open(arquivo, 'w', encoding='utf-8') as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)

estoque_disponivel = carregar_json(ARQUIVO_ESTOQUE_JSON, {})
produtos_disponiveis = carregar_json(ARQUIVO_PRODUTOS_JSON, {})

def salvar_estoque(): salvar_json(ARQUIVO_ESTOQUE_JSON, estoque_disponivel)
def salvar_produtos(): salvar_json(ARQUIVO_PRODUTOS_JSON, produtos_disponiveis)

# --- ESTOQUE ---
estoque_lock = threading.Lock()

def verificar_estoque(produto_id, var=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel: return 0
        if var: return len(estoque_disponivel[produto_id].get("variacoes", {}).get(var, []))
        return len(estoque_disponivel[produto_id].get("itens", []))

def entregar_do_estoque(produto_id, var=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel: return None
        lista = estoque_disponivel[produto_id].get("variacoes", {}).get(var, []) if var else estoque_disponivel[produto_id].get("itens", [])
        if lista:
            item = lista.pop(0)
            salvar_estoque()
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
    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash commands sincronizados")
    async def on_ready(self):
        print(f"🟢 {NOME_LOJA} logado como {self.user}")

bot = ManiaBot()

# --- VIEWS ---
class ConfirmarEntregaView(discord.ui.View):
    def __init__(self, cliente_id, prod_nome, valor, pag_id, var=None):
        super().__init__(timeout=None)
        self.cliente_id, self.prod_nome, self.valor, self.pag_id, self.var = cliente_id, prod_nome, valor, pag_id, var

    @discord.ui.button(label="✅ Confirmar Entrega", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MEU_ID: return
        await interaction.response.defer()
        item = entregar_do_estoque(self.pag_id.split('_')[0], self.var)
        cliente = await bot.fetch_user(self.cliente_id)
        try:
            emb = discord.Embed(title="🎁 SEU PRODUTO CHEGOU!", color=0x00ff88)
            emb.add_field(name="📦 Produto", value=self.prod_nome)
            emb.add_field(name="🔐 Conteúdo", value=f"```{item if item else 'Confirmado manualmente.'}```", inline=False)
            await cliente.send(embed=emb)
        except: pass
        canal = bot.get_channel(CANAL_PAGOS)
        if canal: await canal.send(embed=discord.Embed(title="✅ ENTREGA REALIZADA", description=f"Cliente: <@{self.cliente_id}>\nProduto: {self.prod_nome}", color=0x00ff88))
        for child in self.children: child.disabled = True
        await interaction.edit_original_response(content="✅ Entrega confirmada!", view=self)

class ConfirmarPagamentoView(discord.ui.View):
    def __init__(self, prod_nome, valor, pag_id, var=None):
        super().__init__(timeout=None)
        self.prod_nome, self.valor, self.pag_id, self.var = prod_nome, valor, pag_id, var

    @discord.ui.button(label="💰 Já realizei o pagamento", style=discord.ButtonStyle.primary)
    async def confirmou(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ Dono notificado!", ephemeral=True)
        canal = bot.get_channel(CANAL_CARRINHOS)
        if canal:
            emb = discord.Embed(title="🔔 PAGAMENTO REIVINDICADO", color=0xffaa00)
            emb.add_field(name="Cliente", value=interaction.user.mention)
            emb.add_field(name="Produto", value=self.prod_nome)
            emb.add_field(name="Valor", value=f"R$ {self.valor:.2f}")
            await canal.send(content=f"<@{MEU_ID}>", embed=emb, view=ConfirmarEntregaView(interaction.user.id, self.prod_nome, self.valor, self.pag_id, self.var))
        button.disabled = True
        await interaction.edit_original_response(view=self)

async def gerar_pagamento(interaction, prod_id, nome, preco, var=None):
    pag_id = f"{prod_id}_{interaction.user.id}_{int(time.time())}"
    emb = discord.Embed(title="💸 PAGAMENTO PIX", description=f"**{nome}**\n\n🔑 Chave: `{PIX_EMAIL}`\n💰 Valor: `R$ {preco:.2f}`", color=0x8A05BE)
    try:
        await interaction.user.send(embed=emb, view=ConfirmarPagamentoView(nome, preco, pag_id, var))
        await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("✅ Instruções na DM!", ephemeral=True)
    except:
        await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("❌ DM fechada!", ephemeral=True)

class ProdutoView(discord.ui.View):
    def __init__(self, prod_id, nome, vars=None):
        super().__init__(timeout=None)
        self.prod_id, self.nome, self.vars = prod_id, nome, vars or []
    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success, custom_id="btn_buy")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        info = produtos_disponiveis.get(self.prod_id)
        if not info: return
        if self.vars:
            v = discord.ui.View()
            for var in self.vars:
                btn = discord.ui.Button(label=f"{var['nome']} - R$ {var['preco']:.2f}")
                async def cb(i, p=var['preco'], n=var['nome']): await gerar_pagamento(i, self.prod_id, f"{self.nome} ({n})", p, n)
                btn.callback = cb
                v.add_item(btn)
            await interaction.response.send_message("Escolha:", view=v, ephemeral=True)
        else: await gerar_pagamento(interaction, self.prod_id, self.nome, info['preco'])

# --- COMANDOS ADMIN ---
@bot.tree.command(name="criar_produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str):
    if interaction.user.id != MEU_ID: return
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": descricao, "variacoes": []}
    salvar_produtos()
    await interaction.response.send_message(f"✅ Criado: {nome}", ephemeral=True)

@bot.tree.command(name="configurar_loja")
async def configurar_loja(interaction: discord.Interaction, produto_id: str):
    if interaction.user.id != MEU_ID: return
    p = produtos_disponiveis.get(produto_id)
    if not p: return
    emb = discord.Embed(title=f"⚡ {p['nome']}", description=p['descricao'], color=0xffa500)
    emb.add_field(name="💰 Valor", value=f"R$ {p['preco']:.2f}")
    await interaction.channel.send(embed=emb, view=ProdutoView(produto_id, p['nome'], p.get('variacoes')))
    await interaction.response.send_message("✅ Loja OK!", ephemeral=True)

@bot.tree.command(name="add_estoque")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: str = None):
    if interaction.user.id != MEU_ID: return
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    if produto_id not in estoque_disponivel: estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
    if variacao:
        if "variacoes" not in estoque_disponivel[produto_id]: estoque_disponivel[produto_id]["variacoes"] = {}
        if variacao not in estoque_disponivel[produto_id]["variacoes"]: estoque_disponivel[produto_id]["variacoes"][variacao] = []
        estoque_disponivel[produto_id]["variacoes"][variacao].extend(novos)
    else: estoque_disponivel[produto_id]["itens"].extend(novos)
    salvar_estoque()
    await interaction.response.send_message(f"✅ +{len(novos)} itens", ephemeral=True)

@bot.tree.command(name="gerar_2fa")
async def gerar_2fa(interaction: discord.Interaction, chave: str):
    try: await interaction.response.send_message(f"🔐 Código: `{pyotp.TOTP(chave.strip().upper().replace(' ','')).now()}`", ephemeral=True)
    except: await interaction.response.send_message("❌ Erro", ephemeral=True)

# ===============================
# INÍCIO
# ===============================
if __name__ == "__main__":
    keep_alive() # Inicia o servidor Flask em paralelo
    if not DISCORD_TOKEN: print("❌ Sem Token")
    else: bot.run(DISCORD_TOKEN)
