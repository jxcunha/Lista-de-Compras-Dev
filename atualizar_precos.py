#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atualiza o catalogo de precos do Supermercado Mateus (loja Cohama).

Le a API publica do Mateus Mais (mesma que o site usa) e regrava
produtos_v2.json no formato consumido pelo index.html.

Uso:
    python atualizar_precos.py              # atualiza o JSON
    python atualizar_precos.py --commit     # atualiza, commita e da push
    python atualizar_precos.py --dry-run    # so mostra o que mudaria
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
API = "https://app.mateusmais.com.br/api/products/internal/v1/service/"
INDEX = "SHOWCASE_catalog_product_api_index_PROD"

# Supermercado Mateus Cohama (Sao Luis/MA) - external_code 7
MARKET_ID = "2857c51e-ffc9-4365-b39a-0156cfc032b9"

POR_PAGINA = 1000          # maximo aceito pela API
PAUSA = 1.0                # segundos entre paginas (nao martelar a API)
TENTATIVAS = 3             # retentativas por pagina
TIMEOUT = 60

RAIZ = Path(__file__).resolve().parent
DESTINO = RAIZ / "produtos_v2.json"
UA = "Lista-de-Compras/1.0 (uso pessoal; atualizacao de catalogo)"


# ── Acesso a API ──────────────────────────────────────────────────────────
def buscar_pagina(pagina):
    """Retorna uma pagina de produtos da loja."""
    filtros = json.dumps(
        [[f"market_id:{MARKET_ID}"], ["for_sale:true"]], ensure_ascii=False
    )
    params = (
        f"page={pagina}"
        f"&hitsPerPage={POR_PAGINA}"
        f"&facetFilters={urllib.parse.quote(filtros)}"
    )
    corpo = json.dumps(
        {"facets": [], "params": params, "index": INDEX, "service": "meilisearch"}
    ).encode("utf-8")

    req = urllib.request.Request(
        API,
        data=corpo,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )

    ultimo_erro = None
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            ultimo_erro = e
            if tentativa < TENTATIVAS:
                espera = tentativa * 5
                print(f"    tentativa {tentativa} falhou ({e}); "
                      f"aguardando {espera}s...")
                time.sleep(espera)
    raise RuntimeError(f"pagina {pagina} falhou apos {TENTATIVAS} tentativas: "
                       f"{ultimo_erro}")


# ── Conversao para o formato do app ───────────────────────────────────────
def normalizar_ean(barcode):
    """A API devolve 14 digitos com zero a esquerda; o app usa EAN-13.

    O scanner le codigos de 13 digitos, entao guardamos os ultimos 13
    para que a leitura da camera case com o indice do catalogo.
    """
    ean = (barcode or "").strip()
    return ean[-13:] if len(ean) > 13 else ean


# measure_type que o catalogo grava abreviado
ABREVIACOES = {"unidade": "un"}


def formatar_unidade(h):
    """Reproduz o campo 'u' do catalogo (ex: 'por 500g', '750ml', 'kg', '2.0L')."""
    if h.get("is_bulk"):
        return f"por {h.get('bulk_portion')}g"

    tipo = h.get("measure_type")
    medida = h.get("measure")
    if not tipo:
        return ""

    curto = ABREVIACOES.get(tipo.lower(), tipo.lower())

    if tipo == "L":                       # litro mantem o decimal e a maiuscula
        return f"{medida}L"
    if medida is None or medida in (1, 1.0):   # 1 unidade -> so o tipo
        return curto

    inteiro = int(medida) if float(medida).is_integer() else medida
    return f"{inteiro}{curto}"


def converter(h):
    """API -> registro enxuto usado pelo index.html."""
    return {
        # objectID vem em 100% dos hits; 'id' falta em parte deles
        "id":   h.get("objectID") or h.get("id"),
        "n":    h.get("name"),
        "b":    h.get("brand"),
        "p":    h.get("sale_price"),
        "d":    h.get("departament_name"),
        "s":    h.get("section_name"),
        "i":    h.get("small_image"),
        "img":  h.get("image"),
        "u":    formatar_unidade(h),
        "ean":  normalizar_ean(h.get("barcode")),
    }


def baixar_catalogo():
    """Baixa todas as paginas e devolve a lista de produtos convertidos."""
    print(f"Loja: Supermercado Mateus Cohama")
    print(f"Baixando catalogo ({POR_PAGINA} por pagina)...")

    primeira = buscar_pagina(0)
    total = primeira.get("nbHits", 0)
    paginas = primeira.get("nbPages", 1)
    print(f"  {total} produtos disponiveis em {paginas} paginas")

    hits = list(primeira.get("hits", []))
    for p in range(1, paginas):
        time.sleep(PAUSA)
        pagina = buscar_pagina(p)
        hits.extend(pagina.get("hits", []))
        print(f"  pagina {p + 1}/{paginas} - {len(hits)} produtos", end="\r")

    print(f"  {len(hits)} produtos baixados" + " " * 20)

    # Remove duplicados por id, preservando a ordem
    vistos = set()
    produtos = []
    for h in hits:
        pid = h.get("objectID") or h.get("id")
        if pid and pid not in vistos:
            vistos.add(pid)
            produtos.append(converter(h))

    if len(produtos) != len(hits):
        print(f"  {len(hits) - len(produtos)} duplicados removidos")
    return produtos


# ── Comparacao com o catalogo atual ───────────────────────────────────────
def comparar(antigos, novos):
    """Mostra o que mudou entre o catalogo em disco e o recem-baixado."""
    ant = {p["id"]: p for p in antigos if p.get("id")}
    nov = {p["id"]: p for p in novos if p.get("id")}

    incluidos = len(nov.keys() - ant.keys())
    removidos = len(ant.keys() - nov.keys())

    subiu = desceu = 0
    maiores = []
    for pid in ant.keys() & nov.keys():
        pa, pn = ant[pid].get("p"), nov[pid].get("p")
        if pa is None or pn is None or pa == pn:
            continue
        if pn > pa:
            subiu += 1
        else:
            desceu += 1
        if pa:
            maiores.append((abs(pn - pa) / pa, ant[pid]["n"], pa, pn))

    print("\n--- Mudancas -----------------------------------")
    print(f"  produtos novos ....... {incluidos}")
    print(f"  produtos removidos ... {removidos}")
    print(f"  precos que subiram ... {subiu}")
    print(f"  precos que cairam .... {desceu}")

    maiores.sort(reverse=True)
    if maiores:
        print("\n  Maiores variacoes:")
        for pct, nome, pa, pn in maiores[:8]:
            seta = "+" if pn > pa else "-"
            print(f"    {seta} {pct * 100:5.1f}%  {nome[:42]:<42} "
                  f"R$ {pa:.2f} -> R$ {pn:.2f}")


def gravar(produtos):
    """Grava no mesmo formato compacto do arquivo original."""
    with open(DESTINO, "w", encoding="utf-8") as f:
        json.dump({"produtos": produtos}, f,
                  ensure_ascii=False, separators=(",", ":"))
    tamanho = DESTINO.stat().st_size / 1024 / 1024
    print(f"\n{DESTINO.name} gravado ({tamanho:.1f} MB)")


def commitar(qtd):
    """Commita e da push, no mesmo padrao das mensagens anteriores."""
    hoje = date.today().strftime("%d/%m/%Y")
    msg = f"Atualiza catalogo Mateus Cohama: {qtd} produtos ({hoje})"
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
    ap = argparse.ArgumentParser(description="Atualiza precos do Mateus Cohama")
    ap.add_argument("--commit", action="store_true",
                    help="commita e da push apos atualizar")
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra as mudancas sem gravar o arquivo")
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

    # Protecao: queda abrupta costuma indicar problema na API, nao no catalogo
    if antigos and len(produtos) < len(antigos) * 0.5:
        print(f"\nFALHOU: so {len(produtos)} produtos contra {len(antigos)} "
              f"anteriores. Arquivo preservado por seguranca.")
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
