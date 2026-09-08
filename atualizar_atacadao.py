#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atualiza o catalogo de precos do Atacadao.

Le a API publica de catalogo da VTEX (a mesma que o site usa) e regrava
produtos_atacadao.json no formato consumido pelo index.html.

A VTEX limita cada consulta a 50 itens e trava a paginacao em _from=2500,
entao percorremos a arvore de categorias: cada departamento e consultado
inteiro e, quando bate no teto, descemos para as subcategorias dele.

Uso:
    python atualizar_atacadao.py              # atualiza o JSON
    python atualizar_atacadao.py --commit     # atualiza, commita e da push
    python atualizar_atacadao.py --dry-run    # so mostra o que mudaria
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

# O console do Windows abre em cp1252 e quebra ao imprimir acentos
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

# ── Configuracao ──────────────────────────────────────────────────────────
BASE = "https://www.atacadao.com.br/api/catalog_system/pub"
POR_PAGINA = 50            # maximo aceito pela VTEX
TETO_VTEX = 2500           # a partir daqui a API devolve HTTP 400
# Canal 1: e o preco que o site mostra ao consumidor e que se paga no
# caixa (conferido em produto real: R$ 17,49 no site = sc=1). O canal 2
# devolve precos 12-17% menores que nao correspondem nem ao unitario nem
# ao desconto por quantidade - outro canal de venda, fora do nosso caso.
CANAL = 1
PAUSA = 0.4                # segundos entre requisicoes
TENTATIVAS = 3
TIMEOUT = 45

RAIZ = Path(__file__).resolve().parent
DESTINO = RAIZ / "produtos_atacadao.json"
UA = "Lista-de-Compras/1.0 (uso pessoal; atualizacao de catalogo)"

# Departamentos que interessam para uma lista de compras. Ficam de fora
# Automotivo, Pet Shop, Jardinagem, Utilidades domesticas, Eletronicos,
# Papelaria, Esporte e lazer, Vestuario e Cafeteria.
DEPARTAMENTOS = {
    "Mercearia",
    "Frios e congelados",
    "Bebidas",
    "Higiene e perfumaria",
    "Limpeza",
    "Hortifrúti",
    "Carnes, aves e peixes",
    "Padaria e matinais",
    "Descartáveis e embalagens",
}


def filtro(trilha, de=0, ate=1):
    """Monta a consulta: categoria pelo caminho + so o que esta a venda.

    Sem o filtro de disponibilidade a VTEX devolve o catalogo inteiro,
    mas so os primeiros itens vem com preco - o resto sao produtos que
    a loja lista e nao vende. Filtrar aqui evita baixar 49 mil produtos
    para aproveitar 4 mil.

    O `sc` e obrigatorio: sem ele a API devolve a oferta do canal padrao
    e os precos vem zerados.
    """
    caminho = "C:/" + "/".join(trilha) + "/"
    return (f"/products/search?fq={caminho}"
            f"&fq=isAvailablePerSalesChannel_{CANAL}:1&sc={CANAL}"
            f"&_from={de}&_to={ate}")


def buscar(caminho, so_cabecalho=False):
    """GET na API da VTEX, com retentativa.

    Com so_cabecalho, devolve o total de itens da consulta lendo o
    cabecalho `resources` (formato "0-1/1234") em vez do corpo. Isso
    permite saber o tamanho da categoria antes de pagina-la.
    """
    req = urllib.request.Request(
        f"{BASE}{caminho}",
        headers={"Accept": "application/json", "User-Agent": UA},
    )
    ultimo = None
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                if so_cabecalho:
                    rec = resp.headers.get("resources", "")
                    return int(rec.split("/")[-1]) if "/" in rec else 0
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 400:          # teto de paginacao: nao adianta insistir
                return 0 if so_cabecalho else []
            ultimo = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            ultimo = e
        if tentativa < TENTATIVAS:
            time.sleep(tentativa * 4)
    raise RuntimeError(f"{caminho} falhou apos {TENTATIVAS} tentativas: {ultimo}")


def contar(trilha):
    """Quantos produtos a venda a categoria tem, sem baixar nenhum."""
    return buscar(filtro(trilha), so_cabecalho=True)


# ── Conversao para o formato do app ───────────────────────────────────────
def departamento_e_secao(produto):
    """Extrai departamento e secao de '/Limpeza/Limpeza de cozinha/Esponja/'."""
    caminhos = produto.get("categories") or []
    if not caminhos:
        return "", ""
    # o caminho mais longo e o mais especifico
    partes = [p for p in max(caminhos, key=len).split("/") if p]
    dep = partes[0] if partes else ""
    sec = partes[1] if len(partes) > 1 else ""
    return dep, sec


def normalizar_ean(bruto):
    """Converte o codigo de barras para o formato que o scanner devolve.

    A camera (ZXing) le EAN-13, EAN-8 e UPC-A. A VTEX mistura os tres,
    mais o ITF-14 das caixas, entao padronizamos em EAN-13 sempre que
    possivel e aceitamos EAN-8 como esta. Devolve "" quando nao da para
    aproveitar - o produto entao fica de fora do catalogo.
    """
    ean = (bruto or "").strip()
    if not ean.isdigit():
        return ""
    if len(ean) > 13:                 # ITF-14 e afins: o EAN-13 esta no fim
        ean = ean[-13:]
    elif len(ean) == 12:              # UPC-A vira EAN-13 com um zero na frente
        ean = "0" + ean
    return ean if len(ean) in (8, 13) else ""


def formatar_unidade(item):
    """Reproduz o campo 'u' do catalogo (ex: '1UND', '1PCT', '200g')."""
    tipo = item.get("measurementUnit") or ""
    mult = item.get("unitMultiplier")
    if not tipo:
        return ""
    if mult is None:
        return tipo
    mult = float(mult)
    # Peso variavel: 0.2kg fica mais legivel como 200g, no mesmo padrao
    # que o catalogo do Mateus usa.
    if tipo.lower() == "kg" and mult < 1:
        return f"{round(mult * 1000)}g"
    inteiro = int(mult) if mult.is_integer() else mult
    return f"{inteiro}{tipo}"


def preco_do_item(oferta, item):
    """Preco de UMA unidade do produto.

    A VTEX cota `Price` por unidade de medida e informa em
    `unitMultiplier` quantas dessas medidas cabem em um item. Para
    granel isso muda tudo: a farinha de rosca vem como Price=15.90
    (o quilo) com unitMultiplier=0.2, e o site cobra R$ 3,18 pelo
    pacote de 200g. Sem multiplicar, o app mostraria 5x o valor e a
    comparacao com o Mateus - que grava o preco da porcao - ficaria
    sem sentido.
    """
    preco = oferta.get("Price")
    if not preco or preco <= 0:
        return None
    mult = item.get("unitMultiplier")
    if mult:
        preco = preco * float(mult)
    return round(preco, 2)


def converter(produto):
    """Produto da VTEX -> registro enxuto usado pelo index.html.

    Devolve None quando o produto nao tem oferta valida (sem preco ou
    sem vendedor), para nao poluir o catalogo com item nao compravel.
    """
    itens = produto.get("items") or []
    if not itens:
        return None
    item = itens[0]

    vendedores = item.get("sellers") or []
    if not vendedores:
        return None
    oferta = vendedores[0].get("commertialOffer") or {}
    preco = preco_do_item(oferta, item)
    if preco is None:
        return None

    # EAN opcional: sem ele o produto so nao entra no indice do scanner,
    # mas continua achavel pelo nome no autocomplete - e o que salva
    # carnes, frutas e verduras, que em boa parte nao tem codigo de barras.
    # O catalogo anterior gravava aqui o RefId interno (7-8 digitos),
    # motivo pelo qual a camera nunca encontrou nenhum produto do Atacadao.
    ean = normalizar_ean(item.get("ean"))

    imagens = item.get("images") or []
    img_grande = imagens[0].get("imageUrl", "") if imagens else ""
    if not img_grande:
        return None
    # a VTEX serve o mesmo arquivo em varios tamanhos: g=grande, s=pequeno
    img_peq = img_grande.replace("/g.jpg", "/s.jpg")

    dep, sec = departamento_e_secao(produto)

    return {
        "id":   str(produto.get("productId") or ""),
        "ean":  ean,
        "n":    produto.get("productName") or "",
        "b":    produto.get("brand") or "",
        "p":    float(preco),
        "d":    dep,
        "s":    sec,
        "i":    img_peq,
        "img":  img_grande,
        "u":    formatar_unidade(item),
    }


# ── Varredura do catalogo ─────────────────────────────────────────────────
def paginar(trilha, vistos, produtos):
    """Baixa uma categoria inteira, ja convertendo e filtrando."""
    de = 0
    while de < TETO_VTEX:
        ate = min(de + POR_PAGINA - 1, TETO_VTEX - 1)
        lote = buscar(filtro(trilha, de, ate))
        if not lote:
            return
        for bruto in lote:
            pid = bruto.get("productId")
            if pid in vistos:
                continue
            vistos.add(pid)
            reg = converter(bruto)
            if reg:
                produtos.append(reg)
        if len(lote) < POR_PAGINA:       # acabou a categoria
            return
        de = ate + 1
        time.sleep(PAUSA)


def coletar(nos, vistos, produtos, caminho=(), nivel=0):
    """Percorre a arvore, descendo so nas categorias grandes demais.

    A VTEX so aceita subcategoria pelo caminho inteiro ("C:/2/18/");
    filtrar pelo id solto devolve zero. Por isso carregamos o caminho
    dos ids conforme descemos.

    Consultar o total antes de paginar evita gastar 50 requisicoes para
    so entao descobrir que a categoria estoura o teto da VTEX.
    """
    for no in nos:
        cid, nome = no.get("id"), no.get("name", "?")
        filhos = no.get("children") or []
        recuo = "  " * nivel
        trilha = caminho + (str(cid),)

        total = contar(trilha)
        if not total:
            continue

        if total > TETO_VTEX and filhos:
            print(f"{recuo}  {nome}: {total} a venda, "
                  f"descendo em {len(filhos)} subcategorias", flush=True)
            coletar(filhos, vistos, produtos, trilha, nivel + 1)
            continue

        antes = len(produtos)
        paginar(trilha, vistos, produtos)
        print(f"{recuo}  {nome}: {total} a venda -> +{len(produtos) - antes} "
              f"(acumulado {len(produtos)})", flush=True)
        time.sleep(PAUSA)


def baixar_catalogo():
    print("Loja: Atacadao (catalogo VTEX)", flush=True)
    arvore = buscar("/category/tree/3")

    mercado = [n for n in arvore if n.get("name") in DEPARTAMENTOS]
    fora = [n["name"] for n in arvore if n.get("name") not in DEPARTAMENTOS]
    print(f"  {len(mercado)} departamentos de mercado", flush=True)
    print(f"  fora: {', '.join(fora)}", flush=True)

    print("", flush=True)
    vistos, produtos = set(), []
    coletar(mercado, vistos, produtos)

    print(f"\n  {len(produtos)} produtos aproveitados "
          f"de {len(vistos)} vistos", flush=True)
    return produtos


# ── Comparacao com o catalogo atual ───────────────────────────────────────
def comparar(antigos, novos):
    ant = {p["id"]: p for p in antigos if p.get("id")}
    nov = {p["id"]: p for p in novos if p.get("id")}

    print("\n--- Mudancas -----------------------------------")
    print(f"  produtos novos ....... {len(nov.keys() - ant.keys())}")
    print(f"  produtos removidos ... {len(ant.keys() - nov.keys())}")

    subiu = desceu = reciclados = 0
    maiores = []
    for pid in ant.keys() & nov.keys():
        a, n = ant[pid], nov[pid]
        # O Atacadao reaproveita productId: o mesmo id ja apareceu como
        # "Canela em Po Kitano 8g" e depois como "Kit Salon Line Shampoo".
        # Sem conferir o nome, a comparacao inventa altas de 1900%.
        if a.get("n") != n.get("n"):
            reciclados += 1
            continue
        pa, pn = a.get("p"), n.get("p")
        if not pa or not pn or pa == pn:
            continue
        if pn > pa:
            subiu += 1
        else:
            desceu += 1
        maiores.append((abs(pn - pa) / pa, a["n"], pa, pn))

    print(f"  precos que subiram ... {subiu}")
    print(f"  precos que cairam .... {desceu}")
    if reciclados:
        print(f"  ids reaproveitados ... {reciclados} (produto trocou de nome)")

    maiores.sort(reverse=True)
    if maiores:
        print("\n  Maiores variacoes:")
        for pct, nome, pa, pn in maiores[:8]:
            seta = "+" if pn > pa else "-"
            print(f"    {seta} {pct * 100:5.1f}%  {nome[:42]:<42} "
                  f"R$ {pa:.2f} -> R$ {pn:.2f}")

    com_ean = sum(1 for p in novos if len(p.get("ean") or "") == 13)
    print(f"\n  EAN-13 validos: {com_ean}/{len(novos)} "
          f"(antes: {sum(1 for p in antigos if len(str(p.get('ean') or '')) == 13)})")


def gravar(produtos):
    with open(DESTINO, "w", encoding="utf-8") as f:
        json.dump({"produtos": produtos}, f,
                  ensure_ascii=False, separators=(",", ":"))
    print(f"\n{DESTINO.name} gravado "
          f"({DESTINO.stat().st_size / 1024 / 1024:.1f} MB)")


def commitar(qtd):
    hoje = date.today().strftime("%d/%m/%Y")
    msg = f"Atualiza catalogo Atacadao: {qtd} produtos ({hoje})"
    for cmd in (["git", "add", DESTINO.name],
                ["git", "commit", "-m", msg],
                ["git", "push"]):
        r = subprocess.run(cmd, cwd=RAIZ, capture_output=True, text=True)
        if r.returncode != 0:
            saida = (r.stderr or r.stdout).strip()
            if "nothing to commit" in saida:
                print("Nada mudou - sem commit.")
                return
            print(f"ERRO em `{' '.join(cmd)}`:\n{saida}")
            sys.exit(1)
    print(f"Commitado e enviado: {msg}")


def main():
    ap = argparse.ArgumentParser(description="Atualiza precos do Atacadao")
    ap.add_argument("--commit", action="store_true",
                    help="commita e da push apos atualizar")
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra as mudancas sem gravar o arquivo")
    ap.add_argument("--aceitar-queda", action="store_true",
                    help="grava mesmo com queda grande no numero de produtos")
    args = ap.parse_args()

    antigos = []
    if DESTINO.exists():
        with open(DESTINO, encoding="utf-8") as f:
            antigos = json.load(f).get("produtos", [])
        print(f"Catalogo atual: {len(antigos)} produtos")

    try:
        produtos = baixar_catalogo()
    except RuntimeError as e:
        print(f"\nFALHOU: {e}")
        sys.exit(1)

    if not produtos:
        print("\nFALHOU: a API nao devolveu produtos - arquivo preservado.")
        sys.exit(1)

    if (antigos and len(produtos) < len(antigos) * 0.5
            and not args.aceitar_queda):
        print(f"\nFALHOU: so {len(produtos)} produtos contra {len(antigos)} "
              f"anteriores. Arquivo preservado por seguranca.")
        print("Se a queda for esperada, repita com --aceitar-queda.")
        sys.exit(1)

    if antigos:
        comparar(antigos, produtos)

    if args.dry_run:
        print("\n[--dry-run] nada foi gravado.")
        return

    gravar(produtos)
    if args.commit:
        commitar(len(produtos))


if __name__ == "__main__":
    main()
