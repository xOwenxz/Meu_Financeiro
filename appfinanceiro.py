"""Dashboard financeiro local, sem dependências externas."""

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import json
import os
import tempfile
import threading
import uuid

PASTA = Path(__file__).resolve().parent
ARQUIVO_DADOS = PASTA / "transacoes.json"
HOST, PORTA = "127.0.0.1", 8000
LOCK_TRANSACOES = threading.Lock()


class ErroArmazenamento(Exception):
    """Indica que o arquivo persistido não pode ser lido ou validado."""


def ler_transacoes():
    if not ARQUIVO_DADOS.exists():
        return []
    try:
        transacoes = json.loads(ARQUIVO_DADOS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as erro:
        raise ErroArmazenamento("Não foi possível ler transacoes.json.") from erro
    if not isinstance(transacoes, list) or any(
        not isinstance(item, dict) for item in transacoes
    ):
        raise ErroArmazenamento(
            "transacoes.json não contém uma lista válida de lançamentos."
        )
    return transacoes


def salvar_transacoes(transacoes):
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=PASTA,
            prefix="transacoes-",
            suffix=".tmp",
            delete=False,
        ) as arquivo:
            temporario = Path(arquivo.name)
            json.dump(transacoes, arquivo, ensure_ascii=False, indent=2)
        os.replace(temporario, ARQUIVO_DADOS)
    finally:
        if temporario is not None:
            try:
                temporario.unlink()
            except FileNotFoundError:
                pass


class DashboardHandler(BaseHTTPRequestHandler):
    def responder_json(self, dados, status=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = urlparse(self.path).path
        if caminho == "/api/transacoes":
            try:
                return self.responder_json(ler_transacoes())
            except ErroArmazenamento as erro:
                return self.responder_json({"erro": str(erro)}, 500)
        if caminho in ("/", "/index.html"):
            corpo = (PASTA / "dashboard.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            return self.wfile.write(corpo)
        self.responder_json({"erro": "Página não encontrada."}, 404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/transacoes":
            return self.responder_json({"erro": "Rota não encontrada."}, 404)
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
            if tamanho <= 0 or tamanho > 16_384:
                raise ValueError("O conteúdo enviado está vazio ou é grande demais.")
            dados = json.loads(self.rfile.read(tamanho))
            if not isinstance(dados, dict):
                raise ValueError("Envie os dados do lançamento em formato válido.")
            descricao = str(dados.get("descricao", "")).strip()
            tipo = dados.get("tipo")
            categoria = str(dados.get("categoria", "")).strip()
            observacao = str(dados.get("observacao", "")).strip()
            data_lancamento = str(dados.get("data", ""))
            valor = Decimal(str(dados.get("valor", "")))
            if not valor.is_finite():
                raise ValueError("Informe um valor numérico válido.")
            valor_centavos = int(
                (valor * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            datetime.strptime(data_lancamento, "%Y-%m-%d")
            if not descricao or len(descricao) > 100:
                raise ValueError("Informe uma descrição de até 100 caracteres.")
            if tipo not in ("receita", "despesa"):
                raise ValueError("Selecione receita ou despesa.")
            if not categoria or len(categoria) > 40:
                raise ValueError("Informe uma categoria de até 40 caracteres.")
            if valor_centavos <= 0:
                raise ValueError("O valor deve ser maior que zero.")
            transacao = {
                "id": uuid.uuid4().hex,
                "descricao": descricao,
                "tipo": tipo,
                "categoria": categoria,
                "observacao": observacao[:250],
                "data": data_lancamento,
                "valor_centavos": valor_centavos,
            }
            with LOCK_TRANSACOES:
                transacoes = ler_transacoes()
                transacoes.append(transacao)
                salvar_transacoes(transacoes)
            return self.responder_json(transacao, 201)
        except (ValueError, TypeError, InvalidOperation, json.JSONDecodeError) as erro:
            return self.responder_json({"erro": str(erro) or "Dados inválidos."}, 400)
        except ErroArmazenamento as erro:
            return self.responder_json({"erro": str(erro)}, 500)
        except OSError:
            return self.responder_json({"erro": "Não foi possível salvar o lançamento."}, 500)

    def do_DELETE(self):
        prefixo = "/api/transacoes/"
        caminho = urlparse(self.path).path
        if not caminho.startswith(prefixo):
            return self.responder_json({"erro": "Rota não encontrada."}, 404)
        identificador = caminho[len(prefixo):]
        try:
            with LOCK_TRANSACOES:
                transacoes = ler_transacoes()
                atualizadas = [t for t in transacoes if t.get("id") != identificador]
                if len(atualizadas) == len(transacoes):
                    return self.responder_json({"erro": "Lançamento não encontrado."}, 404)
                salvar_transacoes(atualizadas)
            return self.responder_json({"ok": True})
        except ErroArmazenamento as erro:
            return self.responder_json({"erro": str(erro)}, 500)
        except OSError:
            return self.responder_json({"erro": "Não foi possível excluir o lançamento."}, 500)

    def log_message(self, formato, *args):
        print(f"[{self.log_date_time_string()}] {formato % args}")


if __name__ == "__main__":
    servidor = ThreadingHTTPServer((HOST, PORTA), DashboardHandler)
    print(f"Dashboard disponível em http://{HOST}:{PORTA}")
    print("Para encerrar, pressione Ctrl+C.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        servidor.server_close()
