# pyObscuraProto

<p align="center">
  <a href="https://github.com/kretoffer/pyObscuraProto/actions"><img src="https://img.shields.io/github/actions/workflow/status/kretoffer/pyObscuraProto/autotests.yml?style=for-the-badge&logo=github&label=тесты&color=8A2BE2" alt="Tests"></a>
  <a href="https://github.com/kretoffer/pyObscuraProto/stargazers"><img src="https://img.shields.io/github/stars/kretoffer/pyObscuraProto?style=for-the-badge&logo=githubsponsors&logoColor=FFFFFF&label=звёзды&color=FFD700" alt="Stars"></a>
  <a href="https://github.com/kretoffer/pyObscuraProto/issues"><img src="https://img.shields.io/github/issues/kretoffer/pyObscuraProto?style=for-the-badge&logo=openbugbounty&logoColor=FFFFFF&label=issues&color=FF6B6B" alt="Issues"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.13%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.13+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/kretoffer/pyObscuraProto?style=for-the-badge&logo=libreoffice" alt="License"></a>
</p>

Python-обёртка для C++ библиотеки [ObscuraProto](https://github.com/anomalyco/ObscuraProto) — сквозное шифрование поверх WebSocket.

## Возможности

- **Сквозное шифрование** — протокол Noise (NX pattern) на базе libsodium
- **Аутентификация сервера** — долговременная ключевая пара для подписи, клиент проверяет публичный ключ сервера
- **Автоматическое согласование версий** — клиент и сервер договариваются о версии протокола во время handshake
- **Билдер/ридер бинарных payload'ов** — type-safe fluent API (`PayloadBuilder` / `PayloadReader`)
- **Автоматическая распаковка** — параметры payload'а распаковываются по type hints Python
- **Двунаправленный стриминг** — мультиплексированные потоки данных поверх одного зашифрованного соединения
- **Анонимные и аутентифицированные сессии** — обработка клиентов с/без identity; коллбэки верификации
- **Система конфигурации** — лимиты скорости, соединений, размера сообщений, таймауты; загрузка из YAML или настройка из Python
- **Полная типизация** — все аннотации типов Python, проверка через pyright
- **Высокая производительность** — C++ ядро через pybind11, GIL освобождается во время I/O

## Установка

```bash
pip install pyObscuraProto
```

### Сборка из исходников

```bash
git clone --recurse-submodules https://github.com/anomalyco/pyObscuraProto.git
cd pyObscuraProto
python -m venv .venv && source .venv/bin/activate
pip install cmake
pip install -e .
```

Требуется CMake 3.14+, компилятор C++17 и libsodium (`brew install libsodium` на macOS, `apt install libsodium-dev` на Linux).

## Быстрый старт

```python
import ObscuraProto as op

op.Crypto.init()

server = op.Server()
server.start(9001)

client = op.Client(server.public_key)
client.connect("ws://localhost:9001")

@client.on_ready
def on_ready():
    payload = op.PayloadBuilder(0x1001).add_param("hello").build()
    client.send(payload)
```

Больше примеров в [examples/](examples/).

## Streaming API

Двунаправленные мультиплексированные потоки поверх одного зашифрованного соединения.

```python
import ObscuraProto as op

op.Crypto.init()

# --- Сервер ---
server = op.Server()

@server.on_incoming_stream
def handle_stream(stream: op.Stream):
    @stream.on_data
    def on_data(data: bytes):
        stream.write(b"echo: " + data)

    @stream.on_end
    def on_end():
        stream.end()

server.start(9006)

# --- Клиент ---
client = op.Client(server.public_key)

@client.on_ready
def on_ready():
    stream = client.start_stream()

    @stream.on_data
    def on_data(data: bytes):
        print(f"Echo: {data}")

    stream.write(b"hello")
    stream.end()

client.connect("ws://localhost:9006")
```

Полный пример: [examples/streaming_example.py](examples/streaming_example.py)

## Анонимные и аутентифицированные сессии

Клиенты, подключающиеся **без** identity-ключа, считаются **анонимными** — их сообщения обрабатываются через анонимные хендлеры. Клиенты с подтверждённым Ed25519 identity считаются **аутентифицированными** и используют обычные хендлеры.

### Анонимные хендлеры

```python
@server.on_anon_payload(0x5001)
def handle_anon_register(hdl: op.ConnectionHdl, data: bytes):
    print(f"Анонимная регистрация: {data}")
    server.send_anonymous(hdl, op.PayloadBuilder(0x5001).add_param("ok").build())

@server.on_anon_request(0x5002)
def handle_anon_auth(hdl: op.ConnectionHdl, token: str) -> op.Payload:
    return op.PayloadBuilder(0x5003).add_param(True).build()

@server.anon_default_payload_handler
def handle_anon_default(hdl: op.ConnectionHdl, payload: op.Payload):
    print(f"Необработанный анонимный opcode: {payload.op_code:04x}")
```

### Аутентификация клиента

```python
# --- Сервер ---
server = op.Server()

@server.on_client_identity
def check_identity(hdl: op.ConnectionHdl, pk: op.PublicKey) -> bool:
    # Принимаем только известные публичные ключи
    return pk.data == allowed_key.data

# --- Клиент ---
client = op.Client(server.public_key)
client.set_client_identity(my_identity_keypair)  # Ed25519 ключевая пара
client.connect("ws://localhost:9001")

# Сервер может адресовать клиента по identity:
server.send_to_identity(client_pk, payload)
identity = server.get_client_identity(hdl)
```

Полный пример: [client_identity_example.cpp](https://github.com/kretoffer/ObscuraProto/blob/main/examples/client_identity_example.cpp)

## Конфигурация

ObscuraProto поддерживает гибкую настройку лимитов скорости, соединений, размера сообщений и таймаутов. Создайте объект `Config` и передайте его в `Server` или `Client`:

```python
cfg = op.Config()

# Лимит сообщений — token bucket на соединение
cfg.rate_limit.messages_per_second = 200
cfg.rate_limit.burst_size = 500

# Лимиты соединений — на IP и общий
cfg.connection_limits.max_per_ip = 20
cfg.connection_limits.max_total = 5000

# Лимиты размера сообщений
cfg.message_limits.max_decrypted_payload = 65535

# Таймауты
cfg.timeouts.idle_ms = 600000      # 10 мин бездействия
cfg.timeouts.handshake_ms = 15000  # 15 сек на handshake

server = op.Server(config=cfg)
client = op.Client(server.public_key, config=cfg)
```

Или загрузите из YAML-файла (см. [config_example.yml](https://github.com/kretoffer/ObscuraProto/blob/main/config_example.yml)):

```python
cfg = op.Config.from_yaml("path/to/config.yml")
```

| Поле конфига | По умолчанию | Описание |
|---|---|---|
| `rate_limit.enabled` | `true` | Включить/отключить все лимиты |
| `rate_limit.messages_per_second` | `100` | Макс. сообщений на соединение в секунду |
| `rate_limit.burst_size` | `200` | Размер burst для token bucket |
| `rate_limit.handshake_attempts_per_minute` | `10` | Макс. попыток handshake с IP в минуту |
| `rate_limit.connections_per_minute` | `30` | Макс. новых соединений с IP в минуту |
| `connection_limits.max_per_ip` | `10` | Макс. одновременных соединений с одного IP |
| `connection_limits.max_total` | `1000` | Макс. всего одновременных соединений |
| `message_limits.max_ws_frame_size` | `1048576` | Макс. размер WebSocket фрейма (байт) |
| `message_limits.max_decrypted_payload` | `65535` | Макс. размер расшифрованного payload (байт) |
| `timeouts.handshake_ms` | `10000` | Таймаут handshake (мс) |
| `timeouts.idle_ms` | `300000` | Таймаут бездействия (мс) |
| `timeouts.check_interval_ms` | `5000` | Интервал проверки таймаутов (мс) |

## Справочник API

| Класс / Функция | Описание |
|---|---|
| `Server` | Зашифрованный WebSocket-сервер. Декораторы: `@on_payload(opcode)`, `@on_request(opcode)`, `@on_anon_payload(opcode)`, `@on_anon_request(opcode)`, `@on_incoming_stream`, `@default_payload_handler`, `@anon_default_payload_handler`, `@on_client_identity` |
| `Client(server_pk)` | Зашифрованный WebSocket-клиент. Декораторы: `@on_ready`, `@on_disconnect`, `@on_payload(opcode)`, `@on_request(opcode)`, `@on_incoming_stream` |
| `Stream` | Двунаправленный поток данных. Декораторы: `@on_data`, `@on_end`, `@on_cancel`. I/O: `write()`, `end()`, `cancel()`, `async_write()`, `async_end()`, `async_cancel()` |
| `PayloadBuilder(opcode)` | Сборка бинарных payload'ов. `add_param(str / int / uint / bool / float / bytes)`, `.build()` |
| `PayloadReader(payload)` | Чтение бинарных payload'ов. `read_string()`, `read_int()`, `read_uint()`, `read_bool()`, `read_float()`, `read_bytes()` |
| `Payload` | Сырой payload с полями `.op_code` и `.parameters`. Есть `.serialize()` / `Payload.deserialize()` |
| `uint` | Маркер типа: `def handler(value: uint)` читает параметр как беззнаковое целое |
| `Config` | Конфигурация сервера/клиента. Подструктуры: `rate_limit`, `connection_limits`, `message_limits`, `timeouts`, `opcodes`. Методы: `from_yaml(path)`, `with_defaults()` |
| `Crypto` | Статические криптооперации: `init()`, `generate_kx_keypair()`, `generate_sign_keypair()`, `sign()`, `verify()`, `encrypt()`, `decrypt()` |
| `KeyPair` / `PublicKey` / `PrivateKey` | Типы ключей с полем `.data` |
| `ConnectionHdl` | Непрозрачный идентификатор соединения для адресации конкретных клиентов |

## Примеры

| Пример | Описание |
|---|---|---|
| [python_websocket_example.py](examples/python_websocket_example.py) | Минимальный send/response с авто-распаковкой |
| [request_response_example/](examples/request_response_example/) | Паттерн запрос-ответ (async сервер + клиент) |
| [streaming_example.py](examples/streaming_example.py) | Двунаправленный стриминг echo |
| [client_identity_example.cpp](https://github.com/kretoffer/ObscuraProto/blob/main/examples/client_identity_example.cpp) | Анонимная регистрация + аутентифицированная сессия (C++) |

## Разработка

```bash
source .venv/bin/activate
pip install -e .
pre-commit install
```

- **Ruff** — линтинг и форматирование
- **Pyright** — проверка типов
- **pytest** — тестирование (`python -m pytest tests/`)
- **Pre-commit** — автоматические проверки перед каждым коммитом

Полные правила в [CONTRIBUTING.md](CONTRIBUTING.md).

## Лицензия

MIT © 2025 Kretov Artem. Подробнее в [LICENSE](LICENSE).
