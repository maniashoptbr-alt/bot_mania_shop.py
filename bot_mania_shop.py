import discord
from discord import app_commands
import threading
import os
import time
import json
from datetime import datetime
import pyotp
from flask import Flask

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
def health_check(): return "Mania Shop Online!", 200
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
    async def setup_hook(self):
        await self.tree.sync()
    async def on_ready(self):
        print(f"🟢 {NOME_LOJA} logado como {self.user}")

bot = ManiaBot()

# --- VIEWS DE VENDA ---
class ConfirmarEntregaView(discord.ui.View):
    def __init__(self, cliente_id, prod_nome, pag_id, var=None):
        super().__init__(timeout=None)
        self.cliente_id, self.prod_nome, self.pag_id, self.var = cliente_id, prod_nome, pag_id, var

    @discord.ui.button(label="✅ Confirmar Entrega", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MEU_ID: return
        await interaction.response.defer()
        prod_id = self.pag_id.split('_')[0]
        item = entregar_do_estoque(prod_id, self.var)
        cliente = await bot.fetch_user(self.cliente_id)
        try:
            emb = discord.Embed(title="🎁 SEU PRODUTO CHEGOU!", color=0x00ff88)
            emb.add_field(name="📦 Produto", value=self.prod_nome)
            emb.add_field(name="🔐 Conteúdo", value=f"```{item if item else 'Entrega confirmada pelo dono.'}```", inline=False)
            await cliente.send(embed=emb)
        except: pass
        canal = bot.get_channel(CANAL_PAGOS)
        if canal:
            log = discord.Embed(title="✅ ENTREGA REALIZADA", color=0x00ff88)
            log.add_field(name="Cliente", value=f"<@{self.cliente_id}>")
            log.add_field(name="Produto", value=self.prod_nome)
            await canal.send(embed=log)
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
            view = ConfirmarEntregaView(interaction.user.id, self.prod_nome, self.pag_id, self.var)
            await canal.send(content=f"<@{MEU_ID}>", embed=emb, view=view)
        button.disabled = True
        await interaction.edit_original_response(view=self)

async def fluxo_pagamento(interaction, prod_id, nome, preco, var=None):
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
        p = produtos_disponiveis.get(self.prod_id)
        if not p: return
        if self.vars:
            v = discord.ui.View()
            for var in self.vars:
                btn = discord.ui.Button(label=f"{var['nome']} - R$ {var['preco']:.2f}")
                async def cb(i, p_val=var['preco'], n_val=var['nome']): await fluxo_pagamento(i, self.prod_id, f"{self.nome} ({n_val})", p_val, n_val)
                btn.callback = cb
                v.add_item(btn)
            await interaction.response.send_message("Escolha uma opção:", view=v, ephemeral=True)
        else: await fluxo_pagamento(interaction, self.prod_id, self.nome, p['preco'])

# --- COMANDOS ADMIN ---
@bot.tree.command(name="criar_produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str):
    if interaction.user.id != MEU_ID: return
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": descricao, "imagem": "", "variacoes": []}
    salvar_json("produtos.json", produtos_disponiveis)
    await interaction.response.send_message(f"✅ Criado: {nome}", ephemeral=True)

@bot.tree.command(name="set_imagem")
async def set_imagem(interaction: discord.Interaction, id: str, url: str):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis:
        produtos_disponiveis[id]["imagem"] = url
        salvar_json("produtos.json", produtos_disponiveis)
        await interaction.response.send_message("✅ Imagem atualizada!", ephemeral=True)

@bot.tree.command(name="add_variacao")
async def add_variacao(interaction: discord.Interaction, id: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis:
        produtos_disponiveis[id]["variacoes"].append({"nome": nome, "preco": preco})
        salvar_json("produtos.json", produtos_disponiveis)
        await interaction.response.send_message(f"✅ Variação `{nome}` adicionada!", ephemeral=True)

@bot.tree.command(name="remover_variacao")
async def remover_variacao(interaction: discord.Interaction, id: str, indice: int):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis and 0 <= indice < len(produtos_disponiveis[id]["variacoes"]):
        rem = produtos_disponiveis[id]["variacoes"].pop(indice)
        salvar_json("produtos.json", produtos_disponiveis)
        await interaction.response.send_message(f"✅ Variação `{rem['nome']}` removida!", ephemeral=True)

@bot.tree.command(name="add_estoque")
async def add_estoque(interaction: discord.Interaction, id: str, itens: str, variacao: str = None):
    if interaction.user.id != MEU_ID: return
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    if id not in estoque_disponivel: estoque_disponivel[id] = {"itens": [], "variacoes": {}}
    if variacao:
        if "variacoes" not in estoque_disponivel[id]: estoque_disponivel[id]["variacoes"] = {}
        if variacao not in estoque_disponivel[id]["variacoes"]: estoque_disponivel[id]["variacoes"][variacao] = []
        estoque_disponivel[id]["variacoes"][variacao].extend(novos)
    else: estoque_disponivel[id]["itens"].extend(novos)
    salvar_json("estoque.json", estoque_disponivel)
    await interaction.response.send_message(f"✅ Adicionado {len(novos)} itens!", ephemeral=True)

@bot.tree.command(name="remover_estoque")
async def remover_estoque(interaction: discord.Interaction, id: str, indice: int, variacao: str = None):
    if interaction.user.id != MEU_ID: return
    lista = estoque_disponivel.get(id, {}).get("variacoes", {}).get(variacao, []) if variacao else estoque_disponivel.get(id, {}).get("itens", [])
    if 0 <= indice < len(lista):
        rem = lista.pop(indice)
        salvar_json("estoque.json", estoque_disponivel)
        await interaction.response.send_message(f"✅ Item `{rem}` removido!", ephemeral=True)

@bot.tree.command(name="configurar_loja")
async def configurar_loja(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    p = produtos_disponiveis.get(id)
    if not p: return
    emb = discord.Embed(title=f"⚡ {p['nome']}", description=p['descricao'], color=0xffa500)
    emb.add_field(name="💰 Valor", value=f"R$ {p['preco']:.2f}")
    qtd = verificar_estoque(id)
    if qtd > 0: emb.add_field(name="📦 Estoque", value=f"{qtd} unidades")
    if p.get("imagem"): emb.set_image(url=p["imagem"])
    view = ProdutoView(id, p['nome'], p.get('variacoes'))
    await interaction.channel.send(embed=emb, view=view)
    await interaction.response.send_message("✅ Loja configurada!", ephemeral=True)

@bot.tree.command(name="sincronizar")
async def sincronizar(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID: return
    await bot.tree.sync()
    await interaction.response.send_message("✅ Comandos sincronizados!", ephemeral=True)

@bot.tree.command(name="gerar_2fa")
async def gerar_2fa(interaction: discord.Interaction, chave: str):
    try: await interaction.response.send_message(f"🔐 Código: `{pyotp.TOTP(chave.strip().upper().replace(' ','')).now()}`", ephemeral=True)
    except: await interaction.response.send_message("❌ Erro", ephemeral=True)

# --- EXECUÇÃO ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    if not DISCORD_TOKEN: print("❌ Sem Token")
    else: bot.run(DISCORD_TOKEN)
