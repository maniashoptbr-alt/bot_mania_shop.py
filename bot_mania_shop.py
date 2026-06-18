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
    async def setup_hook(self):
        await self.tree.sync()
    async def on_ready(self):
        print(f"🟢 {NOME_LOJA} logado como {self.user}")

bot = ManiaBot()

# --- UTILITÁRIOS ---
def gerar_qr_code(conteudo):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(conteudo)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

# --- VIEWS PROFISSIONAIS ---

class Modal2FA(discord.ui.Modal, title="Gerador de Código 2FA"):
    chave = discord.ui.TextInput(label="Cole sua chave 2FA aqui", placeholder="Ex: JBSWY3DPEHPK3PXP", required=True)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            totp = pyotp.TOTP(self.chave.value.strip().upper().replace(" ", ""))
            codigo = totp.now()
            tempo = 30 - (int(time.time()) % 30)
            emb = discord.Embed(title="🔐 Autenticação 2FA", color=0x00ff88)
            emb.add_field(name="Código Atual", value=f"```\n{codigo}\n```", inline=False)
            emb.add_field(name="Expira em", value=f"{tempo} segundos")
            await interaction.response.send_message(embed=emb, ephemeral=True)
        except:
            await interaction.response.send_message("❌ Chave inválida!", ephemeral=True)

class Gerador2FAView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Gerar Código 2FA", style=discord.ButtonStyle.success, emoji="🔐")
    async def gerar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(Modal2FA())

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
            emb = discord.Embed(title="🎁 SEU PRODUTO CHEGOU!", description=f"Obrigado por comprar na **{NOME_LOJA}**!", color=0x00ff88)
            emb.add_field(name="📦 Produto", value=self.prod_nome, inline=True)
            emb.add_field(name="🔐 Conteúdo", value=f"```{item if item else 'Entrega confirmada manualmente.'}```", inline=False)
            emb.set_footer(text="Volte sempre!")
            await cliente.send(embed=emb)
        except: pass
        canal = bot.get_channel(CANAL_PAGOS)
        if canal:
            log = discord.Embed(title="✅ VENDA CONCLUÍDA", color=0x00ff88, timestamp=datetime.now())
            log.add_field(name="Cliente", value=f"<@{self.cliente_id}>")
            log.add_field(name="Produto", value=self.prod_nome)
            await canal.send(embed=log)
        for child in self.children: child.disabled = True
        await interaction.edit_original_response(content="✅ Produto entregue com sucesso!", view=self)

class ConfirmarPagamentoView(discord.ui.View):
    def __init__(self, prod_nome, valor, pag_id, var=None):
        super().__init__(timeout=None)
        self.prod_nome, self.valor, self.pag_id, self.var = prod_nome, valor, pag_id, var

    @discord.ui.button(label="💰 Já realizei o pagamento", style=discord.ButtonStyle.primary, emoji="💸")
    async def confirmou(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        agora = datetime.now()
        if user_id in cooldowns_pagamento and agora < cooldowns_pagamento[user_id]:
            restante = (cooldowns_pagamento[user_id] - agora).seconds
            await interaction.response.send_message(f"⚠️ Aguarde {restante}s para confirmar novamente.", ephemeral=True)
            return
        
        cooldowns_pagamento[user_id] = agora + timedelta(minutes=2)
        await interaction.response.send_message("🚀 **Solicitação enviada!** Nossa equipe está verificando seu pagamento. Aguarde a entrega aqui na DM.", ephemeral=True)
        
        canal = bot.get_channel(CANAL_CARRINHOS)
        if canal:
            emb = discord.Embed(title="🛒 NOVO PEDIDO AGUARDANDO", color=0xffaa00, timestamp=datetime.now())
            emb.add_field(name="👤 Cliente", value=interaction.user.mention)
            emb.add_field(name="📦 Produto", value=self.prod_nome)
            emb.add_field(name="💰 Valor", value=f"R$ {self.valor:.2f}")
            emb.set_footer(text=f"ID: {self.pag_id}")
            await canal.send(content=f"<@{MEU_ID}>", embed=emb, view=ConfirmarEntregaView(interaction.user.id, self.prod_nome, self.pag_id, self.var))

async def fluxo_pagamento(interaction, prod_id, nome, preco, var=None):
    pag_id = f"{prod_id}_{interaction.user.id}_{int(time.time())}"
    emb = discord.Embed(title="💳 FINALIZAR PAGAMENTO", description=f"Você escolheu: **{nome}**\nSiga as instruções abaixo para receber seu produto.", color=0x8A05BE)
    emb.add_field(name="🔑 Chave PIX (E-mail)", value=f"```{PIX_EMAIL}```", inline=False)
    emb.add_field(name="💰 Valor a pagar", value=f"```R$ {preco:.2f}```", inline=False)
    emb.set_image(url="attachment://qrcode.png")
    emb.set_footer(text="Escaneie o QR Code acima para pagar via Nubank.")
    
    qr_buffer = gerar_qr_code(PIX_EMAIL)
    file = discord.File(fp=qr_buffer, filename="qrcode.png")
    
    view = ConfirmarPagamentoView(nome, preco, pag_id, var)
    try:
        await interaction.user.send(file=file, embed=emb, view=view)
        await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("✅ **Pedido Gerado!** Verifique suas mensagens diretas (DM).", ephemeral=True)
    except:
        await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("❌ **Erro!** Sua DM está fechada. Ative-a para receber o pagamento.", ephemeral=True)

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

# --- COMANDOS ADMINISTRATIVOS ---

@bot.tree.command(name="criar_produto", description="[ADMIN] Registrar novo produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str):
    if interaction.user.id != MEU_ID: return
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": descricao, "imagem": "", "variacoes": []}
    salvar_json("produtos.json", produtos_disponiveis)
    await interaction.response.send_message(f"✅ Produto `{nome}` registrado!", ephemeral=True)

@bot.tree.command(name="excluir_produto", description="[ADMIN] Remover um produto permanentemente")
async def excluir_produto(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis:
        del produtos_disponiveis[id]
        if id in estoque_disponivel: del estoque_disponivel[id]
        salvar_json("produtos.json", produtos_disponiveis)
        salvar_json("estoque.json", estoque_disponivel)
        await interaction.response.send_message(f"🗑️ Produto `{id}` excluído.", ephemeral=True)

@bot.tree.command(name="ver_indices", description="[ADMIN] Ver índices de estoque e variações")
async def ver_indices(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    p = produtos_disponiveis.get(id)
    if not p: return await interaction.response.send_message("❌ ID não encontrado.", ephemeral=True)
    
    txt = f"📊 **GERENCIAMENTO: {p['nome']}**\n\n"
    txt += "**🔹 VARIAÇÕES:**\n"
    for i, v in enumerate(p.get("variacoes", [])):
        txt += f"`{i}` - {v['nome']} (R$ {v['preco']:.2f})\n"
    
    txt += "\n**📦 ESTOQUE GERAL:**\n"
    for i, it in enumerate(estoque_disponivel.get(id, {}).get("itens", [])):
        txt += f"`{i}` - `{it}`\n"
    
    for var_nome, itens in estoque_disponivel.get(id, {}).get("variacoes", {}).items():
        txt += f"\n**📦 ESTOQUE ({var_nome}):**\n"
        for i, it in enumerate(itens):
            txt += f"`{i}` - `{it}`\n"
    
    await interaction.response.send_message(txt if len(txt) < 2000 else "⚠️ Muita informação! Limpe o estoque.", ephemeral=True)

@bot.tree.command(name="remover_variacao", description="[ADMIN] Remover variação por índice")
async def remover_variacao(interaction: discord.Interaction, id: str, indice: int):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis and 0 <= indice < len(produtos_disponiveis[id]["variacoes"]):
        rem = produtos_disponiveis[id]["variacoes"].pop(indice)
        salvar_json("produtos.json", produtos_disponiveis)
        await interaction.response.send_message(f"✅ Variação `{rem['nome']}` removida.", ephemeral=True)

@bot.tree.command(name="configurar_loja", description="[ADMIN] Postar embed de venda")
async def configurar_loja(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    p = produtos_disponiveis.get(id)
    if not p: return
    emb = discord.Embed(title=f"⚡ {p['nome']}", description=p['descricao'].replace("|", "\n"), color=0xffa500)
    emb.add_field(name="💰 A partir de", value=f"R$ {p['preco']:.2f}", inline=True)
    emb.add_field(name="📦 Disponível", value=f"{verificar_estoque(id)} un.", inline=True)
    if p.get("imagem"): emb.set_image(url=p["imagem"])
    emb.set_footer(text=f"ID: {id} | Mania Shop")
    await interaction.channel.send(embed=emb, view=ProdutoView(id, p['nome'], p.get('variacoes')))
    await interaction.response.send_message("✅ Vitrine atualizada!", ephemeral=True)

@bot.tree.command(name="configurar_2fa", description="[ADMIN] Enviar painel de 2FA profissional")
async def configurar_2fa(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID: return
    emb = discord.Embed(title="🔐 GERADOR DE CÓDIGO 2FA", description="Utilize nosso gerador seguro para obter seus códigos de acesso.\n\n1️⃣ Clique no botão abaixo\n2️⃣ Insira sua chave secreta\n3️⃣ Receba seu código instantaneamente", color=0x00ff88)
    emb.set_footer(text="Segurança & Praticidade | Mania Shop")
    await interaction.channel.send(embed=emb, view=Gerador2FAView())
    await interaction.response.send_message("✅ Painel 2FA enviado!", ephemeral=True)

# --- REUTILIZANDO COMANDOS ANTERIORES ---
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
    await interaction.response.send_message(f"✅ +{len(novos)} itens adicionados.", ephemeral=True)

@bot.tree.command(name="add_variacao")
async def add_variacao(interaction: discord.Interaction, id: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis:
        produtos_disponiveis[id]["variacoes"].append({"nome": nome, "preco": preco})
        salvar_json("produtos.json", produtos_disponiveis)
        await interaction.response.send_message(f"✅ Variação `{nome}` adicionada!", ephemeral=True)

@bot.tree.command(name="set_imagem")
async def set_imagem(interaction: discord.Interaction, id: str, url: str):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis:
        produtos_disponiveis[id]["imagem"] = url
        salvar_json("produtos.json", produtos_disponiveis)
        await interaction.response.send_message("✅ Imagem atualizada!", ephemeral=True)

@bot.tree.command(name="sincronizar")
async def sincronizar(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID: return
    await bot.tree.sync()
    await interaction.response.send_message("✅ Comandos sincronizados!", ephemeral=True)

# ===============================
# INÍCIO
# ===============================
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    if not DISCORD_TOKEN: print("❌ Sem Token")
    else: bot.run(DISCORD_TOKEN)
