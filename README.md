# Tool Rent Bot (Telegram)

Телеграм-бот для учёта аренды инструмента. Стек: Docker, Python 3.11, aiogram 3, APScheduler, SQLite.

## Навигация
- [1. Быстрый старт](#1-быстрый-старт-github--запуск)
- [2. Требования](#2-требования)
- [3. Установка Docker](#3-установка-docker)
- [4. Структура проекта](#4-структура-проекта)
- [5. Настройка окружения](#5-настройка-окружения)
- [6. Запуск](#6-запуск-windowslinuxmacos)
- [7. Каталог инструментов](#7-каталог-инструментов)
- [8. Использование](#8-использование)
- [9. Уведомления и отчёты](#9-уведомления-и-отчёты)
- [10. Хранилище данных](#10-хранилище-данных)
- [11. Смена часового пояса](#11-смена-часового-пояса)
- [12. Импорт catalogcsv с хоста](#12-импорт-catalogcsv-с-хоста)
- [13. Обновление из GitHub](#13-обновление-из-github)
- [14. Частые проблемы](#14-частые-проблемы)
- [15. Разработка](#15-разработка)

## 1. Быстрый старт (GitHub → запуск)
1) Клонируйте репозиторий:
```bash
git clone https://github.com/USER/tool_rent_bot.git
cd tool_rent_bot
```
2) Создайте `bot/.env` (см. раздел «[5. Настройка окружения](#5-настройка-окружения)»).
3) Запустите:
```bash
docker compose up -d --build
```
4) Откройте логи: `docker compose logs -f bot`

## 2. Требования
- Docker (Windows/macOS: Docker Desktop; Linux: Docker Engine + compose)
- Токен бота Telegram (получить у @BotFather)

## 3. Установка Docker

- Windows (Docker Desktop + WSL2)
  1. Установите Docker Desktop с официального сайта
  2. Включите WSL2 (если не включён)
  3. Запустите Docker Desktop и дождитесь статуса "Running"

- macOS (Docker Desktop)
  1. Установите Docker Desktop с официального сайта
  2. Запустите и дождитесь статуса "Running"

- Linux (Ubuntu)
  ```bash
  sudo apt update && sudo apt install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt update
  sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker $USER  # перелогиньтесь
  ```
  Проверка:
  ```bash
  docker version && docker compose version
  ```

## 4. Структура проекта
```
tool_rent_bot/
├─ bot/
│  ├─ main.py
│  ├─ bot_handlers.py
│  ├─ database.py
│  ├─ scheduler.py
│  ├─ utils.py
│  ├─ requirements.txt
│  └─ .env               # создаёте сами
├─ Dockerfile
├─ docker-compose.yml
└─ README.md
```

## 5. Настройка окружения
Создайте файл `bot/.env`:
```env
BOT_TOKEN=ВАШ_ТОКЕН
TZ=Asia/Tokyo
# ADMIN_ID=123456789   # опционально: админ для общей сводки
```
- `TZ` — часовой пояс для расписаний и дат (по умолчанию Asia/Tokyo).

## 6. Запуск (Windows/Linux/macOS)
В корне проекта выполните:
```bash
docker compose up -d --build
```
Проверка и логи:
```bash
docker compose ps
docker compose logs -f bot
```
Перезапуск/остановка:
```bash
docker compose restart bot
docker compose down
```

## 7. Каталог инструментов
- Импорт при старте из `/app/data/catalog.csv` (формат CSV: `Название,Цена`).
- Импорт из чата: отправьте CSV-файл боту — он сохранится и импортируется.
- Команды:
  - `/catalog` — список с кнопками редактирования (изменить название/цену, удалить)
  - `/setprice <название> <цена>` — добавить/обновить позицию каталога
  - `/import_catalog` — импорт `/app/data/catalog.csv` из volume

Пример CSV:
```
Перфоратор Bosch,500
Шуруповёрт Makita,300
```

## 8. Использование
- Добавление аренды: отправьте `Инструмент 500` или только `Инструмент` (если есть в каталоге).
- Главное меню (`/start`):
  - `📋 Список аренд` — активные аренды + сумма
  - `📊 Отчёт сейчас` — отчёт за сегодня
  - `📅 Отчёт по дате` — бот запросит дату `YYYY-MM-DD` и покажет отчёт
  - `📚 Каталог` — список инструментов с кнопками редактирования
  - `⬆️ Импорт CSV` — подсказка по импорту файла в чат
  - `💵 Установить цену` — подсказка по `/setprice`
- Доп. команды:
  - `/list`, `/report_today`, `/report YYYY-MM-DD`, `/report_now`
  - `/expire_last` — тест уведомления об окончании
  - `/reset_db` — очистка БД (подтверждение)

## 9. Уведомления и отчёты
- Через 24ч после добавления аренды бот шлёт уведомление с кнопками:
  - `✅ Продлить аренду` — продлевает на +24ч
  - `❌ Забрал инструмент` — завершает аренду
- Ежедневно в 21:00 (TZ) — отчёт пользователям с активными арендами (активные + выручка за день).
- В 23:59 (TZ) — фиксация выручки за день для активных аренд.

## 10. Хранилище данных
- База: SQLite в volume `/app/data/rentals.db`
- Загрузки CSV: `/app/data/uploads/`

## 11. Смена часового пояса
- Измените `TZ` в `bot/.env` и перезапустите:
```bash
docker compose restart bot
```

## 12. Импорт catalog.csv с хоста
- Положите `catalog.csv` рядом с проектом и выполните:
  - Linux/macOS:
    ```bash
    docker compose cp ./catalog.csv bot:/app/data/catalog.csv
    docker compose exec bot python -c "import database; import asyncio; from database import import_catalog_from_csv; asyncio.run(import_catalog_from_csv('/app/data/catalog.csv')); print('OK')"
    ```
  - Windows PowerShell:
    ```powershell
    docker compose cp .\catalog.csv bot:/app/data/catalog.csv
    docker compose exec bot python -c "import database; import asyncio; from database import import_catalog_from_csv; asyncio.run(import_catalog_from_csv('/app/data/catalog.csv')); print('OK')"
    ```
- Альтернатива: просто отправьте CSV-файл в чат боту — он импортируется автоматически.

## 13. Обновление из GitHub
```bash
git pull
# при изменении зависимостей
docker compose up -d --build
# иначе достаточно
docker compose restart bot
```

## 14. Частые проблемы
- "Cannot connect to the Docker daemon" — запустите Docker Desktop/демон
- Дата отчёта неверная — проверьте `TZ` в `bot/.env`, перезапустите контейнер
- Ошибка HTML — используйте поддерживаемые теги (`<b>`, `<code>`, и т.п.)

## 15. Разработка
Логи:
```bash
docker compose logs -f bot
```
Пересборка после изменения зависимостей:
```bash
docker compose up -d --build
```

## Быстрый старт (GitHub → запуск)
1) Клонируйте репозиторий:
```bash
git clone https://github.com/hewtyy/rent_instrument_bot.git
cd bot_rent_instr
```
2) Создайте `bot/.env` (см. раздел «Настройка окружения»).
3) Запустите:
```bash
docker compose up -d --build
```
4) Проверьте логи: `docker compose logs -f bot`

## Импорт catalog.csv с хоста
- Положите файл `catalog.csv` рядом с проектом и выполните:
  - Linux/macOS:
    ```bash
    docker compose cp ./catalog.csv bot:/app/data/catalog.csv
    docker compose exec bot python -c "import database; import asyncio; from database import import_catalog_from_csv; asyncio.run(import_catalog_from_csv('/app/data/catalog.csv')); print('OK')"
    ```
  - Windows PowerShell:
    ```powershell
    docker compose cp .\catalog.csv bot:/app/data/catalog.csv
    docker compose exec bot python -c "import database; import asyncio; from database import import_catalog_from_csv; asyncio.run(import_catalog_from_csv('/app/data/catalog.csv')); print('OK')"
    ```
- Альтернатива: отправьте CSV-файл прямо в чат боту — он импортируется автоматически.

## Обновление из GitHub
```bash
git pull
# при изменении зависимостей
docker compose up -d --build
# иначе достаточно
docker compose restart bot
```
