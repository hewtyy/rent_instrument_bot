# Tool Rent Bot (Telegram)

Телеграм-бот для учёта аренды инструмента. Стек: Docker, Python 3.11, aiogram 3, APScheduler, SQLite.

## Требования
- Docker (Windows/macOS: Docker Desktop; Linux: Docker Engine + compose)
- Токен бота Telegram (получить у @BotFather)

## Установка Docker

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

## Структура
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

## Настройка окружения
Создайте файл `bot/.env`:
```env
BOT_TOKEN=ВАШ_ТОКЕН
TZ=Asia/Tokyo
# ADMIN_ID=123456789   # опционально: админ для общей сводки
```
- `TZ` — часовой пояс для расписаний и дат (по умолчанию Asia/Tokyo).

## Запуск (Windows/Linux/macOS)
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

## Каталог инструментов
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

## Использование
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

## Уведомления и отчёты
- Через 24ч после добавления аренды бот шлёт уведомление с кнопками:
  - `✅ Продлить аренду` — продлевает на +24ч
  - `❌ Забрал инструмент` — завершает аренду
- Ежедневно в 21:00 (TZ) — отчёт пользователям с активными арендами (активные + выручка за день).
- В 23:59 (TZ) — фиксация выручки за день для активных аренд.

## Хранилище данных
- База: SQLite в volume `/app/data/rentals.db`
- Загрузки CSV: `/app/data/uploads/`

## Смена часового пояса
- Измените `TZ` в `bot/.env` и перезапустите:
```bash
docker compose restart bot
```

## Частые проблемы
- "Cannot connect to the Docker daemon" — запустите Docker Desktop/демон
- Дата отчёта неверная — проверьте `TZ` в `bot/.env`, перезапустите контейнер
- Ошибка HTML — используйте поддерживаемые теги (`<b>`, `<code>`, и т.п.)

## Разработка
Логи:
```bash
docker compose logs -f bot
```
Пересборка после изменения зависимостей:
```bash
docker compose up -d --build
```
