# Avenor Trading Bot: System Architecture

This document provides a detailed technical overview of the Avenor trading bot's foundational architecture. It describes the system's design principles, components, communication protocols, and operational procedures.

## Avenor System Messaging Diagram

This diagram illustrates the flow of information between the core services via the central ZeroMQ Message Bus Proxy. All services communicate by publishing messages to the bus and subscribing to the topics they are interested in.

```
+--------------------------+                         +--------------------------+
|   market_data.service    |                         |    strategy.service      |
|  ("World State" Pillar)  |                         |   ("Decision" Pillar)    |
+--------------------------+                         +--------------------------+
           |                                                     |      ^
           | PUB: PRICE.ZZZ                                      |      | SUB: PRICE.ZZZ
           | PUB: PRICE.TIGR                                     |      | SUB: ACCOUNT.BALANCE
           | PUB: ACCOUNT.BALANCE                                |      | SUB: POSITION.ZZZ
           | PUB: POSITION.ZZZ                                   |      | SUB: TRADE_CONFIRMATION
           | PUB: HEARTBEAT.MARKET_DATA                          |      |
           |                                                     |      | PUB: TRADE_ORDER.CREATE
           v                                                     v      | PUB: HEARTBEAT.STRATEGY
+---------------------------------------------------------------------------------+
|                                                                                 |
|                      ZMQ Message Bus (proxy.service)                            |
|     (Listens on ZMQ_INBOUND_BUS_URL, Broadcasts on ZMQ_OUTBOUND_BUS_URL)        |
|                                                                                 |
+---------------------------------------------------------------------------------+
           ^                                                     ^      |
           | PUB: TRADE_CONFIRMATION                             |      |
           | PUB: HEARTBEAT.EXECUTION                            |      |
           |                                                     |      |
           | SUB: TRADE_ORDER.CREATE                             |      |
           |                                                     |      |
+--------------------------+                         +--------------------------+
|    execution.service     |                         |   healthcheck.service    |
|    ("Action" Pillar)     |                         |    (System Monitor)      |
+--------------------------+                         +--------------------------+
                                                                 ^
                                                                 | SUB: HEARTBEAT.*
                                                                 | (Listens for all heartbeats)
```

### How to Read the Diagram:

- **Arrows Pointing to the Bus (`v`):** Indicate a service is **publishing** (sending) messages. The label on the arrow shows the topic of the message (e.g., `PUB: PRICE.ZZZ`).
    
- **Arrows Pointing Away from the Bus (`^`):** Indicate a service is **subscribing** (receiving) messages. The label shows which topics it is listening for (e.g., `SUB: PRICE.ZZZ`).
    
- **Central Hub:** The `proxy.service` acts as the central switchboard, ensuring that a message published by any service is broadcast to all other services that are subscribed to that topic.

## 1. Core Architectural Design

The Avenor trading bot is engineered as a **decoupled, multi-service application**. The architecture prioritizes resilience, observability, and maintainability by separating distinct logical functions into independent, long-running processes managed by the operating system.

### Key Design Principles:

- **Separation of Concerns:** Each service (or "pillar") has a single, clearly defined responsibility. This isolation prevents failures in one component from cascading and simplifies development and debugging.
    
- **Asynchronous Communication:** Services communicate through a central, non-blocking message bus. This eliminates direct dependencies between services, allowing them to be developed, deployed, and restarted independently.
    
- **Layered Fault Tolerance:** The system employs multiple layers of monitoring and recovery, from low-level process supervision to high-level application health checks.
    
- **Configuration-Driven:** All environment-specific settings, such as network addresses and credentials, are externalized into a configuration file, allowing the application to be deployed in different environments without code changes.
    

## 2. Service Topology

The application is composed of five primary services that run concurrently:

|Service|Logical Role|Core Function|
|---|---|---|
|**`proxy.service`**|Central Message Bus|Acts as the central nervous system, receiving all messages and broadcasting them to relevant subscribers.|
|**`market_data.service`**|The "World State" Pillar|Responsible for all interaction with external market data sources (e.g., broker APIs) to fetch prices and account info.|
|**`strategy.service`**|The "Decision" Pillar|Consumes data from the message bus, applies trading logic, and makes all trading decisions.|
|**`execution.service`**|The "Action" Pillar|Subscribes to trade orders from the strategy pillar, executes them via the broker API, and persists the results.|
|**`healthcheck.service`**|The System Monitor|Observes the health of the application by listening for heartbeats and logs critical alerts if a service goes silent.|

## 3. Communication Protocol: ZeroMQ Message Bus

To achieve a high degree of decoupling and performance, the system's inter-service communication is built on **ZeroMQ (pyzmq)** using a single, unified **PUB/SUB** pattern.

### How It Works:

1. **The Proxy:** A central `proxy.service` runs a ZeroMQ `XSUB/XPUB` device. This proxy binds to two well-known ports: an **inbound URL** for receiving messages and an **outbound URL** for broadcasting them.
    
2. **Publishing:** When a service needs to send information (e.g., a price update, a trade order, a heartbeat), it creates a `zmq.PUB` socket and `connects` to the proxy's inbound URL. It then sends a multipart message consisting of a `topic` (e.g., `b"PRICE.ZZZ"`) and a JSON-encoded `payload`.
    
3. **Subscribing:** When a service needs to receive information, it creates a `zmq.SUB` socket, `connects` to the proxy's outbound URL, and `subscribes` to the topics it is interested in (e.g., `b"PRICE."` to receive all price updates).
    

This architecture was chosen over direct service-to-service connections (like `REQ/REP` or `PUSH/PULL`) to simplify the system. Services do not need to know the addresses of other services; they only need to know the address of the central bus. This makes the system highly flexible and easy to extend.

## 4. State Persistence & The Safe Trade Lifecycle

### Database

- **Technology:** The system uses **PostgreSQL** for its persistent data storage. This was chosen for its robustness, reliability, and support for structured data.
    
- **`trades` Table:** The primary table acts as an immutable ledger of all trading _actions_ attempted by the system. It includes an `is_test_trade` boolean flag to cleanly separate data generated during testing from live production data.
    

### Safe Trade Lifecycle

To ensure no trade order is lost during a service crash or restart, the `execution` service implements a strict state-transition protocol:

1. **Intent:** Upon receiving a `TRADE_ORDER` message, the service's first action is to write a record to the `trades` table with a status of **`PENDING`**. This durably logs the _intent_ to trade before any interaction with the broker.
    
2. **Action:** Only after the `PENDING` record is successfully written does the service submit the trade to the broker's API.
    
3. **Resolution:** Based on the API response, the service updates the database record to a final state, such as **`FILLED`** or **`FAILED`**.
    
4. **Recovery:** On startup, the `execution` service runs a recovery routine. It queries the database for any trades still in a `PENDING` state, which indicates a crash occurred in a previous run. It then queries the broker API to determine the final status of these trades and updates the database accordingly.
    

## 5. Monitoring & Fault Tolerance

The system's reliability is ensured by two complementary monitoring mechanisms.

### Layer 1: Process Supervision (`systemd` Watchdog)

This layer protects against low-level process **crashes** and **freezes**.

- **Configuration:** Each `.service` file is configured with `Restart=on-failure` and `Type=notify` with a `WatchdogSec=30` directive.
    
- **Mechanism:**
    
    - `Restart=on-failure`: If a service exits with a non-zero error code (crashes), `systemd` will automatically restart it.
        
    - `WatchdogSec`: Each Python service uses the `sdnotify` library to send a "ping" to `systemd` on every main loop iteration. If `systemd` does not receive this ping within the 30-second window, it assumes the process is frozen in a deadlock and forcibly kills and restarts it.
        

### Layer 2: Application Health (Heartbeat System)

This layer protects against application-level failures where a process might be running but unable to communicate correctly.

- **Mechanism:**
    
    - Each of the core pillars (`market_data`, `strategy`, `execution`) publishes a `HEARTBEAT.<service_name>` message to the message bus every 15 seconds.
        
    - The dedicated `healthcheck.service` subscribes to all `HEARTBEAT.*` topics.
        
    - It maintains a stateful dictionary of the last time it heard from each service. If more than a set threshold (e.g., 45 seconds) passes without a heartbeat from a specific service, it logs a `CRITICAL` alert. This signals a failure that the automatic `systemd` restarts could not solve, requiring human intervention.
        

## 6. Deployment & Operations

The application is designed to be managed entirely from the command line on its Ubuntu server.

- **Configuration:** All configuration is stored in a single `.env` file in the project root. This file is loaded by each service upon startup using the `python-dotenv` library's `find_dotenv()` function, making each service self-sufficient.
    
- **Management:** All five services are managed as `systemd` user services.
    
    - **Start/Stop/Restart All:** `systemctl --user restart *.service`
        
    - **Check Status:** `systemctl --user status *.service`
        
    - **View Live Logs:** `journalctl --user -f -u <service_name>.service`