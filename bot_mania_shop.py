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

# --- LÓGICA PIX BR CODE ---
def gerar_pix_br_code(valor, chave):
    def f(id, val): return f"{id}{len(str(val)):02}{val}"
    gui = f("00", "BR.GOV.BCB.PIX")
    key = f("01", chave)
    merchant_info = f("26", gui + key)
    payload = [
        f("00", "01"), merchant_info, f("52", "0000"), f("53", "986"),
        f("54", f"{valor:.2f}"), f("58", "BR"), f("59", "MANIA SHOP"),
        f("60", "SAO PAULO"), f("62", f("05", "MANIA")),
    ]
    pix_string = "".join(payload) + "6304"
    crc = 0xFFFF
    for char in pix_string:
        crc ^= (ord(char) << 8)
        for _ in range(8):
            if crc & 0x8000: crc = (crc << 1) ^ 0x1021
            else: crc <<= 1
    return pix_string + f"{crc & 0xFFFF:04X}"

# --- ESTOQUE ---
estoque_lock = threading.Lock()

def verificar_estoque(prod_id, var=None):
    with estoque_lock:
        if prod_id not in estoque_disponivel: return 0
        if var: return len(estoque_disponivel.get(prod_id, {}).get("variacoes", {}).get(var, []))
        return len(estoque_disponivel.get(prod_id, {}).get("itens", []))

def contar_estoque_detalhado(prod_id):
    """Retorna contagem detalhada do estoque"""
    if prod_id not in estoque_disponivel:
        return {"total": 0, "itens": 0, "variacoes": {}}
    
    total = 0
    itens = len(estoque_disponivel[prod_id].get("itens", []))
    total += itens
    
    variacoes = {}
    for var_nome, var_itens in estoque_disponivel[prod_id].get("variacoes", {}).items():
        qtd = len(var_itens)
        variacoes[var_nome] = qtd
        total += qtd
    
    return {"total": total, "itens": itens, "variacoes": variacoes}

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

# --- VIEWS E MODAIS ---

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
        await interaction.response.send_message("🚀 **Solicitação enviada!** Verificando seu pagamento.", ephemeral=True)
        canal = bot.get_channel(CANAL_CARRINHOS)
        if canal:
            emb = discord.Embed(title="🛒 NOVO PEDIDO", color=0xffaa00)
            emb.add_field(name="👤 Cliente", value=interaction.user.mention)
            emb.add_field(name="📦 Produto", value=self.prod_nome)
            emb.add_field(name="💰 Valor", value=f"R$ {self.valor:.2f}")
            await canal.send(content=f"<@{MEU_ID}>", embed=emb, view=ConfirmarEntregaView(interaction.user.id, self.prod_nome, self.pag_id, self.var))

async def fluxo_pagamento(interaction, prod_id, nome, preco, var=None):
    if verificar_estoque(prod_id, var) <= 0:
        return await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("❌ **Esgotado!**", ephemeral=True)
    pix_string = gerar_pix_br_code(preco, PIX_EMAIL)
    qr = qrcode.make(pix_string); buf = BytesIO(); qr.save(buf, format="PNG"); buf.seek(0)
    emb = discord.Embed(title="💳 PAGAMENTO", description=f"Produto: **{nome}**", color=0x8A05BE)
    emb.add_field(name="💰 Valor", value=f"```R$ {preco:.2f}```")
    emb.set_image(url="attachment://qrcode.png")
    try:
        await interaction.user.send(file=discord.File(fp=buf, filename="qrcode.png"), embed=emb, view=ConfirmarPagamentoView(nome, preco, f"{prod_id}_{interaction.user.id}", pix_string, var))
        await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("✅ Verifique sua DM!", ephemeral=True)
    except: await (interaction.followup.send if interaction.response.is_done() else interaction.response.send_message)("❌ DM fechada!", ephemeral=True)

class ProdutoView(discord.ui.View):
    def __init__(self, prod_id, nome, vars=None):
        super().__init__(timeout=None)
        self.prod_id, self.nome, self.vars = prod_id, nome, vars or []
    @discord.ui.button(label="🛒 Adquirir Agora", style=discord.ButtonStyle.success)
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
            await interaction.response.send_message("✨ Escolha uma opção:", view=v, ephemeral=True)
        else: await fluxo_pagamento(interaction, self.prod_id, self.nome, p['preco'])

# --- COMANDOS ADMIN ---

@bot.tree.command(name="criar_produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str):
    if interaction.user.id != MEU_ID: return
    desc_formatada = descricao.replace("|", "\n")
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": desc_formatada, "imagem": "", "variacoes": []}
    salvar_json("produtos.json", produtos_disponiveis); await interaction.response.send_message(f"✅ `{nome}` criado!", ephemeral=True)

@bot.tree.command(name="excluir_produto")
async def excluir_produto(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    if id in produtos_disponiveis:
        del produtos_disponiveis[id]
        if id in estoque_disponivel: del estoque_disponivel[id]
        salvar_json("produtos.json", produtos_disponiveis)
        salvar_json("estoque.json", estoque_disponivel)
        await interaction.response.send_message(f"🗑️ Produto `{id}` excluído com sucesso!", ephemeral=True)
    else: await interaction.response.send_message("❌ ID não encontrado.", ephemeral=True)

@bot.tree.command(name="ver_indices")
async def ver_indices(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    p = produtos_disponiveis.get(id); e = estoque_disponivel.get(id, {})
    if not p: return await interaction.response.send_message("❌ ID não encontrado.", ephemeral=True)
    txt = f"📊 **GERENCIAMENTO: {p['nome']}**\n\n**📦 ESTOQUE GERAL:**\n"
    for i, it in enumerate(e.get("itens", [])): txt += f"`{i}` - `{it}`\n"
    for var_nome, itens in e.get("variacoes", {}).items():
        txt += f"\n**📦 ESTOQUE ({var_nome}):**\n"
        for i, it in enumerate(itens): txt += f"`{i}` - `{it}`\n"
    await interaction.response.send_message(txt[:2000], ephemeral=True)

@bot.tree.command(name="remover_item_estoque")
async def remover_item_estoque(interaction: discord.Interaction, id: str, indice: int, variacao: str = None):
    if interaction.user.id != MEU_ID: return
    if id not in estoque_disponivel: return await interaction.response.send_message("❌ Sem estoque.", ephemeral=True)
    lista = estoque_disponivel[id].get("variacoes", {}).get(variacao, []) if variacao else estoque_disponivel[id].get("itens", [])
    if 0 <= indice < len(lista):
        rem = lista.pop(indice); salvar_json("estoque.json", estoque_disponivel)
        await interaction.response.send_message(f"🗑️ Removido: `{rem}`", ephemeral=True)
    else: await interaction.response.send_message("❌ Índice inválido.", ephemeral=True)

# NOVO COMANDO: Sincronizar produto em canal específico
@bot.tree.command(name="sincronizar_canal")
async def sincronizar_canal(interaction: discord.Interaction, id: str, canal: discord.TextChannel):
    """
    Sincroniza um produto em um canal específico
    Exemplo: /sincronizar_canal id:produto1 canal:#vendas
    """
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    
    p = produtos_disponiveis.get(id)
    if not p:
        await interaction.response.send_message(f"❌ Produto com ID `{id}` não encontrado!", ephemeral=True)
        return
    
    qtd = verificar_estoque(id)
    estoque_detalhado = contar_estoque_detalhado(id)
    
    # Cria embed do produto
    emb = discord.Embed(
        title=f"⚡ {p['nome']}", 
        description=p['descricao'], 
        color=0xffa500 if qtd > 0 else 0xff0000
    )
    emb.add_field(name="💰 Valor", value=f"R$ {p['preco']:.2f}", inline=True)
    emb.add_field(name="📦 Estoque", value=f"{qtd} un." if qtd > 0 else "ESGOTADO", inline=True)
    
    # Mostra detalhes do estoque
    detalhes_estoque = f"Total: {estoque_detalhado['total']} itens\n"
    detalhes_estoque += f"Gerais: {estoque_detalhado['itens']} itens\n"
    if estoque_detalhado['variacoes']:
        detalhes_estoque += "Variações:\n"
        for var, qtd_var in estoque_detalhado['variacoes'].items():
            detalhes_estoque += f"  • {var}: {qtd_var} itens\n"
    emb.add_field(name="📊 Detalhes do Estoque", value=detalhes_estoque, inline=False)
    
    if p.get("imagem"): 
        emb.set_image(url=p["imagem"])
    
    emb.set_footer(text=f"📌 Produto sincronizado em {canal.name} • {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    
    # Envia para o canal escolhido
    try:
        await canal.send(embed=emb, view=ProdutoView(id, p['nome'], p.get('variacoes')))
        await interaction.response.send_message(
            f"✅ Produto `{p['nome']}` sincronizado com sucesso no canal {canal.mention}!\n"
            f"📦 Estoque disponível: {qtd} itens",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Erro ao enviar para o canal: {str(e)}", ephemeral=True)

# NOVO COMANDO: Sincronizar e mostrar contagem real do estoque
@bot.tree.command(name="sincronizar_estoque")
async def sincronizar_estoque(interaction: discord.Interaction, id: str):
    """
    Sincroniza e mostra a contagem real do estoque de um produto
    Exemplo: /sincronizar_estoque id:produto1
    """
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    
    if id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto com ID `{id}` não encontrado!", ephemeral=True)
        return
    
    # Recarrega os dados do estoque
    global estoque_disponivel
    estoque_disponivel = carregar_json("estoque.json", {})
    
    p = produtos_disponiveis[id]
    qtd = verificar_estoque(id)
    estoque_detalhado = contar_estoque_detalhado(id)
    
    # Cria embed com informações detalhadas
    emb = discord.Embed(
        title="📊 SINCRONIZAÇÃO DE ESTOQUE",
        description=f"Contagem real do produto: **{p['nome']}**",
        color=0x00ff88
    )
    
    emb.add_field(
        name="📦 Status Geral",
        value=f"✅ Estoque sincronizado\n"
              f"🆔 ID: `{id}`\n"
              f"💰 Preço: R$ {p['preco']:.2f}",
        inline=False
    )
    
    # Mostra contagem detalhada
    emb.add_field(
        name="📊 Contagem Real",
        value=f"**Total de itens:** {estoque_detalhado['total']}\n"
              f"**Itens gerais:** {estoque_detalhado['itens']}\n"
              f"**Variações:** {len(estoque_detalhado['variacoes'])}",
        inline=False
    )
    
    # Mostra cada variação
    if estoque_detalhado['variacoes']:
        vars_text = ""
        for var, qtd_var in estoque_detalhado['variacoes'].items():
            vars_text += f"• **{var}:** {qtd_var} itens\n"
        emb.add_field(name="🎨 Variações Disponíveis", value=vars_text, inline=False)
    
    # Mostra os itens específicos
    if id in estoque_disponivel:
        itens_text = ""
        itens_gerais = estoque_disponivel[id].get("itens", [])
        if itens_gerais:
            for i, item in enumerate(itens_gerais[:5]):  # Mostra até 5 itens
                itens_text += f"`{i+1}.` {item}\n"
            if len(itens_gerais) > 5:
                itens_text += f"... e mais {len(itens_gerais) - 5} itens"
            emb.add_field(name="📋 Primeiros Itens do Estoque", value=itens_text or "Nenhum item listado", inline=False)
    
    emb.set_footer(text=f"Última atualização: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    await interaction.response.send_message(embed=emb, ephemeral=True)

@bot.tree.command(name="configurar_loja")
async def configurar_loja(interaction: discord.Interaction, id: str):
    if interaction.user.id != MEU_ID: return
    p = produtos_disponiveis.get(id)
    if not p: return
    qtd = verificar_estoque(id)
    emb = discord.Embed(title=f"⚡ {p['nome']}", description=p['descricao'], color=0xffa500 if qtd > 0 else 0xff0000)
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
    emb = discord.Embed(title="🔐 GERADOR 2FA", description="Clique abaixo para gerar seu código de acesso.", color=0x00ff88)
    await interaction.channel.send(embed=emb, view=Gerador2FAView())
    await interaction.response.send_message("✅ Painel 2FA enviado!", ephemeral=True)

@bot.tree.command(name="atualizar_produto")
async def atualizar_produto(interaction: discord.Interaction, id: str, novo_nome: str = None, novo_preco: float = None, nova_descricao: str = None):
    """
    Atualiza as informações de um produto existente mantendo o estoque
    """
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    
    if id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto com ID `{id}` não encontrado!", ephemeral=True)
        return
    
    if not any([novo_nome, novo_preco is not None, nova_descricao]):
        await interaction.response.send_message(
            "❌ Você precisa fornecer pelo menos um campo para atualizar!\n"
            "Exemplo: `/atualizar_produto id:produto1 novo_nome:'Novo Nome'`",
            ephemeral=True
        )
        return
    
    produto = produtos_disponiveis[id]
    atualizacoes = []
    
    if novo_nome:
        produto["nome"] = novo_nome
        atualizacoes.append(f"📝 Nome: `{novo_nome}`")
    
    if novo_preco is not None:
        produto["preco"] = novo_preco
        atualizacoes.append(f"💰 Preço: `R$ {novo_preco:.2f}`")
    
    if nova_descricao:
        desc_formatada = nova_descricao.replace("|", "\n")
        produto["descricao"] = desc_formatada
        preview = nova_descricao[:50] + "..." if len(nova_descricao) > 50 else nova_descricao
        atualizacoes.append(f"📄 Descrição: `{preview}`")
    
    salvar_json("produtos.json", produtos_disponiveis)
    
    qtd_estoque = verificar_estoque(id)
    qtd_variacoes = len(produto.get("variacoes", []))
    
    emb = discord.Embed(
        title="✅ PRODUTO ATUALIZADO COM SUCESSO!",
        description=f"Produto `{id}` atualizado com sucesso!",
        color=0x00ff88
    )
    
    if atualizacoes:
        emb.add_field(
            name="📋 Alterações realizadas:",
            value="\n".join(atualizacoes),
            inline=False
        )
    
    emb.add_field(
        name="📦 Status do Estoque:",
        value=f"• Itens disponíveis: `{qtd_estoque}`\n"
              f"• Variações: `{qtd_variacoes}`\n"
              f"• ID do produto: `{id}`",
        inline=False
    )
    
    emb.add_field(
        name="📦 Dados completos do produto:",
        value=f"**Nome:** {produto['nome']}\n"
              f"**Preço:** R$ {produto['preco']:.2f}\n"
              f"**Descrição:** {produto['descricao'][:100]}{'...' if len(produto['descricao']) > 100 else ''}",
        inline=False
    )
    
    if produto.get("variacoes"):
        vars_text = ""
        for v in produto["variacoes"]:
            vars_text += f"• {v['nome']} - R$ {v['preco']:.2f}\n"
        emb.add_field(
            name="🎨 Variações disponíveis:",
            value=vars_text or "Nenhuma variação cadastrada",
            inline=False
        )
    
    emb.set_footer(text=f"Atualizado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    
    await interaction.response.send_message(embed=emb, ephemeral=True)

# --- EXECUÇÃO ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    if DISCORD_TOKEN: bot.run(DISCORD_TOKEN)
