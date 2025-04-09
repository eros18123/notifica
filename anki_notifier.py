import os
import time
import json
import sqlite3
import ctypes
import threading
from win10toast import ToastNotifier

# Caminho para o banco de dados do Anki
ANKI_DB_PATH = os.path.expanduser("~/Anki/Usuário 1/collection.anki2")

# Caminho para o arquivo de configurações
SETTINGS_PATH = os.path.expanduser("~/.anki_notifier_settings.json")

# Configuração de notificações
toaster = ToastNotifier()

class AnkiNotifier:
    def __init__(self):
        self.notification_interval = 5  # Intervalo padrão de 5 minutos
        self.load_settings()
        self.setup_tray_icon()
        self.start_notification_loop()

    def load_settings(self):
        """Carrega as configurações do arquivo JSON."""
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as file:
                    self.settings = json.load(file)
                    self.notification_interval = self.settings.get("notification_interval", 5)
            except Exception as e:
                print(f"Erro ao carregar configurações: {e}")
                self.settings = {}
        else:
            self.settings = {}

    def save_settings(self):
        """Salva as configurações no arquivo JSON."""
        self.settings["notification_interval"] = self.notification_interval
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as file:
                json.dump(self.settings, file, indent=4)
        except Exception as e:
            print(f"Erro ao salvar configurações: {e}")

    def setup_tray_icon(self):
        """Adiciona um ícone na bandeja do sistema."""
        # Usando ctypes para criar um ícone na bandeja do sistema
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # Oculta a janela do console

    def start_notification_loop(self):
        """Inicia o loop de notificações."""
        def notification_loop():
            while True:
                due_cards = self.get_due_cards_count()
                if due_cards > 0:
                    message = f"Você tem {due_cards} cartão{'s' if due_cards != 1 else ''} para estudar!"
                    toaster.show_toast("Anki", message, duration=10)
                time.sleep(self.notification_interval * 60)  # Converte minutos para segundos

        # Inicia o loop em uma thread separada
        threading.Thread(target=notification_loop, daemon=True).start()

    def get_due_cards_count(self):
        """Retorna o número de cartões pendentes."""
        try:
            conn = sqlite3.connect(ANKI_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM cards WHERE queue = 0")  # Cartões novos
            new_cards = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM cards WHERE queue = 1 AND due <= ?", (int(time.time()),))  # Cartões devidos
            due_cards = cursor.fetchone()[0]
            conn.close()
            return new_cards + due_cards
        except Exception as e:
            print(f"Erro ao acessar o banco de dados do Anki: {e}")
            return 0

if __name__ == "__main__":
    notifier = AnkiNotifier()
    while True:
        time.sleep(1)  # Mantém o script rodando