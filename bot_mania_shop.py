import discord
from discord import app_commands
import threading
import os
import time
import json
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
def health_check():
    return "Mania Shop Online!", 200

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

produtos_disponiveis = carregar_json("produtos.json", {})

def salvar_json(arq, dados):
    with open(arq, 'w', encoding='utf-8') as f: json.dump(dados, f, indent=2, ensure_ascii=False)

# --- DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

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
    if not p:
        await interaction.response.send_message("❌ Produto não encontrado!", ephemeral=True)
        return
    emb = discord.Embed(title=f"⚡ {p['nome']}", description=p['descricao'], color=0xffa500)
    emb.add_field(name="💰 Valor", value=f"R$ {p['preco']:.2f}")
    await interaction.channel.send(embed=emb)
    await interaction.response.send_message("✅ Loja OK!", ephemeral=True)

# --- EXECUÇÃO ---
if __name__ == "__main__":
    # 1. Inicia o Flask em segundo plano
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. Inicia o bot no processo principal
    if not DISCORD_TOKEN:
        print("❌ ERRO: DISCORD_TOKEN não encontrado nas variáveis de ambiente!")
    else:
        print("🚀 Tentando conectar ao Discord...")
        bot.run(DISCORD_TOKEN)
