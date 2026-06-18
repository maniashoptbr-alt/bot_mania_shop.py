import discord
from discord import app_commands
import threading
import os
import time
import json
import pyotp
from flask import Flask

# --- SERVIDOR WEB PARA O RENDER ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Mania Shop Online!", 200

# --- CONFIGURAÇÕES ---
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
MEU_ID = 1516951987868925983
CANAL_CARRINHOS = 1516955638930870365
CANAL_PAGOS = 1516955638930870366
PIX_EMAIL = "maniashoptbr@gmail.com"
NOME_LOJA = "Mania Shop"

# --- PERSISTÊNCIA ---
def carregar_json(arq, default):
    if os.path.exists(arq):
        try:
            with open(arq, 'r', encoding='utf-8') as f: return json.load(f)
        except: return default
    return default

produtos_disponiveis = carregar_json("produtos.json", {})
estoque_disponivel = carregar_json("estoque.json", {})

def salvar_json(arq, dados):
    with open(arq, 'w', encoding='utf-8') as f: json.dump(dados, f, indent=2, ensure_ascii=False)

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

# --- VIEWS ---
class ConfirmarEntregaView(discord.ui.View):
    def __init__(self, cliente_id, prod_nome, pag_id):
        super().__init__(timeout=None)
        self.cliente_id, self.prod_nome, self.pag_id = cliente_id, prod_nome, pag_id
    @discord.ui.button(label="✅ Confirmar Entrega", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != MEU_ID: return
        await interaction.response.defer()
        cliente = await bot.fetch_user(self.cliente_id)
        try:
            await cliente.send(f"🎁 **{self.prod_nome}** entregue com sucesso!")
        except: pass
        for child in self.children: child.disabled = True
        await interaction.edit_original_response(content="✅ Entrega confirmada!", view=self)

class ConfirmarPagamentoView(discord.ui.View):
    def __init__(self, nome, preco, pag_id):
        super().__init__(timeout=None)
        self.nome, self.preco, self.pag_id = nome, preco, pag_id
    @discord.ui.button(label="💰 Já realizei o pagamento", style=discord.ButtonStyle.primary)
    async def confirmou(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ Dono notificado!", ephemeral=True)
        canal = bot.get_channel(CANAL_CARRINHOS)
        if canal:
            emb = discord.Embed(title="🔔 PAGAMENTO REIVINDICADO", color=0xffaa00)
            emb.add_field(name="Cliente", value=interaction.user.mention)
            emb.add_field(name="Produto", value=self.nome)
            await canal.send(content=f"<@{MEU_ID}>", embed=emb, view=ConfirmarEntregaView(interaction.user.id, self.nome, self.pag_id))

# --- COMANDOS ---
@bot.tree.command(name="criar_produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str):
    if interaction.user.id != MEU_ID: return
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": descricao}
    salvar_json("produtos.json", produtos_disponiveis)
    await interaction.response.send_message(f"✅ Criado: {nome}", ephemeral=True)

@bot.tree.command(name="configurar_loja")
async def configurar_loja(interaction: discord.Interaction, produto_id: str):
    if interaction.user.id != MEU_ID: return
    p = produtos_disponiveis.get(produto_id)
    if not p: return
    emb = discord.Embed(title=f"⚡ {p['nome']}", description=p['descricao'], color=0xffa500)
    emb.add_field(name="💰 Valor", value=f"R$ {p['preco']:.2f}")
    await interaction.channel.send(embed=emb, view=ProdutoCompraView(produto_id, p['nome']))
    await interaction.response.send_message("✅ Loja OK!", ephemeral=True)

class ProdutoCompraView(discord.ui.View):
    def __init__(self, prod_id, nome):
        super().__init__(timeout=None)
        self.prod_id, self.nome = prod_id, nome
    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success)
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = produtos_disponiveis.get(self.prod_id)
        pag_id = f"{self.prod_id}_{interaction.user.id}_{int(time.time())}"
        emb = discord.Embed(title="💸 PAGAMENTO PIX", description=f"🔑 Chave: `{PIX_EMAIL}`\n💰 Valor: `R$ {p['preco']:.2f}`", color=0x8A05BE)
        try:
            await interaction.user.send(embed=emb, view=ConfirmarPagamentoView(self.nome, p['preco'], pag_id))
            await interaction.response.send_message("✅ Instruções na DM!", ephemeral=True)
        except: await interaction.response.send_message("❌ DM fechada!", ephemeral=True)

# --- EXECUÇÃO ---
def run_bot():
    if DISCORD_TOKEN: bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    # Inicia o bot em uma thread separada
    threading.Thread(target=run_bot, daemon=True).start()
    # O Flask roda no processo principal para o Gunicorn/Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
