# Academic Project Presentation

## Smart Predictive Maintenance System Using Vibration Data

**Format:** 12-slide technical academic project review  
**Duration:** 10-15 minutes  
**Audience:** University project guide, review panel, evaluators, and student peers  
**Primary message:** The system reduces unplanned machine downtime by converting vibration patterns into early, actionable maintenance alerts.

---

## Slide 1: Smart Predictive Maintenance Using Vibration Data

**Key message:** A low-cost IoT and ML prototype can detect machine health degradation before failure.

**Bullet content:**

- Smart Predictive Maintenance System
- Uses vibration data to monitor machine condition
- Detects healthy, degrading, or faulty behavior
- Team members: `<Name 1>`, `<Name 2>`, `<Name 3>`
- Guide/Professor: `<Guide Name>`

**Speaker notes:**

Introduce the project as a condition-monitoring system for machines such as motors, pumps, fans, and bearings. Explain that the aim is not just to collect sensor data, but to convert vibration behavior into useful maintenance decisions before a serious breakdown occurs.

**Suggested diagram or visual:**

Hero visual showing a machine connected to a vibration sensor, processing system, dashboard, and alert.

**Mermaid code:**

```mermaid
flowchart LR
    Machine["Machine"] --> Sensor["Vibration Sensor"]
    Sensor --> Processor["Processing + ML"]
    Processor --> Dashboard["Dashboard"]
    Processor --> Alert["Maintenance Alert"]
```

---

## Slide 2: Problem Statement

**Key message:** Traditional maintenance often detects faults too late or wastes resources through fixed schedules.

**Bullet content:**

- Machines show early fault signs through vibration changes
- Manual and scheduled maintenance may miss early degradation
- Sudden breakdowns cause downtime, cost, and safety risk
- Goal: detect abnormal vibration early and support planned maintenance

**Speaker notes:**

Explain that machines usually do not fail instantly. Before failure, vibration patterns often change due to imbalance, wear, misalignment, looseness, or bearing problems. The challenge is that traditional methods do not continuously observe these subtle changes.

**Suggested diagram or visual:**

Problem funnel from small vibration change to major failure, showing where early detection helps.

**Mermaid code:**

```mermaid
flowchart LR
    A["Small vibration change"] --> B["Degradation begins"]
    B --> C["Fault becomes visible"]
    C --> D["Machine failure"]
    B -. early warning .-> E["Predictive maintenance action"]
```

---

## Slide 3: Existing Methods / Current Approaches

**Key message:** Current approaches are useful, but they are either late, fixed, manual, threshold-limited, or costly.

**Bullet content:**

- Reactive maintenance repairs after failure
- Scheduled maintenance services at fixed intervals
- Manual inspection depends on technician experience
- Threshold monitoring detects only large abnormal changes
- Industrial systems can be costly for small-scale use

**Speaker notes:**

Walk through the current maintenance methods and their limitations. Position the proposed system as a middle path: more intelligent than simple manual or threshold systems, but suitable for an academic prototype using accessible hardware and software.

**Suggested diagram or visual:**

Comparison diagram of existing approaches and their limitations.

**Mermaid code:**

```mermaid
flowchart LR
    subgraph Current["Existing approaches"]
        Reactive["Reactive<br/>after failure"]
        Scheduled["Scheduled<br/>fixed interval"]
        Manual["Manual<br/>inspection"]
        Threshold["Threshold<br/>fixed limit"]
        Industrial["Industrial<br/>costly setup"]
    end

    subgraph Limits["Main limitations"]
        Late["Late detection"]
        Waste["Unnecessary service"]
        Skill["Depends on skill"]
        FalseAlarm["Misses or false alarms"]
        Cost["High cost and complexity"]
    end

    Reactive --> Late
    Scheduled --> Waste
    Manual --> Skill
    Threshold --> FalseAlarm
    Industrial --> Cost
```

---

## Slide 4: Proposed System

**Key message:** The proposed system converts vibration readings into health status, severity, and maintenance alerts.

**Bullet content:**

- Captures vibration data from sensor or dataset
- Processes and extracts useful signal features
- Classifies machine state as healthy, degrading, or faulty
- Generates alerts with severity and recommendation
- Displays current status and historical trends

**Speaker notes:**

Describe the solution at a high level. The system observes vibration, cleans the data, extracts features such as RMS and peak value, uses a model or rule logic to classify health, and then communicates the result through dashboard and alerts.

**Suggested diagram or visual:**

High-level architecture from machine layer to application layer.

**Mermaid code:**

```mermaid
flowchart LR
    subgraph Field["Machine and sensing"]
        Machine["Machine"]
        Sensor["Vibration sensor"]
        Machine --> Sensor
    end

    subgraph Edge["Data acquisition"]
        Gateway["Microcontroller / Gateway"]
        Sensor --> Gateway
    end

    subgraph Intelligence["Processing and ML"]
        Preprocess["Preprocess"]
        Features["Extract features"]
        Model["Classify health"]
        Severity["Score severity"]
        Preprocess --> Features --> Model --> Severity
    end

    subgraph App["Application"]
        Store[("History + alerts")]
        Dashboard["Dashboard / report"]
        Alert["Alert notification"]
    end

    Gateway --> Preprocess
    Severity --> Store
    Store --> Dashboard
    Severity --> Alert
```

---

## Slide 5: System Architecture

**Key message:** The architecture is modular, so each function can be tested and improved independently.

**Bullet content:**

- Sensor module collects raw vibration readings
- Communication module sends timestamped data
- Processing module cleans and windows the signal
- ML module predicts machine condition
- Storage and dashboard modules preserve evidence and show results

**Speaker notes:**

Emphasize the modular design decision. Separating modules reduces development risk and makes future improvements easier. For example, the classifier can be upgraded without rewriting the dashboard, and the sensor input can be replaced by a dataset during demonstration.

**Suggested diagram or visual:**

Component-level architecture with module interactions.

**Mermaid code:**

```mermaid
flowchart TB
    SensorModule["Sensor Module<br/>collect readings"]
    CommModule["Communication Module<br/>send data"]
    Preprocess["Preprocessing<br/>clean, normalize, window"]
    FeatureExtraction["Feature Extraction<br/>RMS, peak, mean, std"]
    Prediction["Prediction Module<br/>health class + confidence"]
    Alerting["Alert Module<br/>severity + recommendation"]
    Storage[("Storage<br/>history + alerts")]
    Dashboard["Dashboard / Report"]

    SensorModule --> CommModule --> Preprocess
    Preprocess --> FeatureExtraction --> Prediction --> Alerting
    FeatureExtraction --> Storage
    Prediction --> Storage
    Alerting --> Storage
    Storage --> Dashboard
    Alerting --> Dashboard
```

---

## Slide 6: Data Flow and Module Interactions

**Key message:** Raw vibration data becomes usable maintenance evidence through a staged processing pipeline.

**Bullet content:**

- Input: acceleration readings, timestamp, machine ID, sensor ID
- Processing: validation, normalization, windowing, feature extraction
- Output: health class, confidence, severity, alert, trend
- Stored history supports review and future model improvement

**Speaker notes:**

Explain the transformation of data. The important outcome is that the system does not only store raw values; it converts them into features, predictions, alerts, and trends that are useful for maintenance decisions.

**Suggested diagram or visual:**

Data flow diagram from raw readings to dashboard and alert history.

**Mermaid code:**

```mermaid
flowchart LR
    Machine["Machine vibration"] --> Collect["Collect readings"]
    Collect --> Raw[("Raw data")]
    Raw --> Clean["Clean + normalize"]
    Clean --> Window["Create signal windows"]
    Window --> Features["Extract features"]
    Features --> Predict["Predict health state"]
    Predict --> Results[("Prediction results")]
    Predict --> Alert["Generate alert if abnormal"]
    Alert --> AlertLog[("Alert history")]
    Results --> Dashboard["Dashboard trends"]
    AlertLog --> Dashboard
```

---

## Slide 7: User Workflow

**Key message:** The user workflow is simple: monitor, receive alert, inspect, and plan maintenance.

**Bullet content:**

- Operator views machine health status
- System detects abnormal vibration automatically
- Alert includes severity and possible issue
- Supervisor reviews trend and schedules maintenance
- Human decision remains part of the process

**Speaker notes:**

Show that the system supports maintenance users rather than replacing them. The operator receives understandable status and alerts, while the supervisor uses trend evidence to decide priority and timing.

**Suggested diagram or visual:**

User journey diagram showing setup, monitoring, alert response, and review.

**Mermaid code:**

```mermaid
journey
    title Predictive Maintenance User Journey
    section Setup
      Attach sensor or load dataset: 3: Student Developer
      Start data collection: 4: Student Developer
    section Monitoring
      Analyze vibration data: 5: System
      View current health status: 4: Operator
    section Alert Response
      Detect abnormal pattern: 5: System
      Receive severity alert: 4: Operator
      Inspect machine: 4: Operator
    section Decision
      Review historical trend: 4: Supervisor
      Schedule maintenance: 5: Supervisor
```

---

## Slide 8: Sequence Diagram - Typical End-to-End Workflow

**Key message:** The complete workflow connects sensor readings, ML prediction, alerting, and dashboard update.

**Bullet content:**

- Sensor captures vibration signal
- Gateway sends timestamped readings
- Pipeline extracts features and predicts health
- Abnormal condition triggers alert
- Dashboard updates current status and history

**Speaker notes:**

Use this slide to explain the typical runtime behavior step by step. Focus on the value of the loop: every reading can become a status update, a trend point, or an early warning alert.

**Suggested diagram or visual:**

End-to-end sequence diagram for monitoring and alert generation.

**Mermaid code:**

```mermaid
sequenceDiagram
    autonumber
    participant M as Machine
    participant S as Sensor
    participant G as Gateway
    participant P as Processing Pipeline
    participant ML as ML / Rule Model
    participant DB as Storage
    participant A as Alert Manager
    participant UI as Dashboard
    actor O as Operator

    M->>S: Produce vibration signal
    S->>G: Send raw reading
    G->>P: Transmit timestamped batch
    P->>P: Clean, normalize, window
    P->>P: Extract RMS, peak, mean, std
    P->>ML: Submit feature vector
    ML-->>P: Return health state and confidence
    P->>DB: Store features and result
    alt Degrading or faulty
        P->>A: Request alert
        A->>DB: Store alert record
        A-->>UI: Push alert and severity
        UI-->>O: Show recommendation
    else Healthy
        DB-->>UI: Update normal status
    end
```

---

## Slide 9: Implementation Details

**Key message:** The prototype uses practical, accessible technologies while preserving a path to future scaling.

**Bullet content:**

- Hardware: vibration sensor or accelerometer, Arduino/ESP32/Raspberry Pi
- Communication: Serial, Wi-Fi, MQTT, HTTP, or Bluetooth
- Processing: Python-based signal cleaning and feature extraction
- Storage: CSV, SQLite, local files, or cloud database
- Dashboard: web dashboard, desktop UI, or structured report
- Model: ML classifier or rule-based baseline for health state detection

**Speaker notes:**

Keep the implementation discussion concise. Mention that the exact technology can vary depending on available hardware. The important design choice is the modular pipeline, which allows the same logic to work with live sensor input or a stored dataset.

**Suggested diagram or visual:**

Technology stack diagram grouped by hardware, communication, processing, storage, and UI.

**Mermaid code:**

```mermaid
flowchart TB
    Hardware["Hardware<br/>Sensor + microcontroller"]
    Communication["Communication<br/>Serial / Wi-Fi / MQTT / HTTP"]
    Processing["Processing<br/>Python + signal features"]
    Model["Model<br/>ML classifier or rules"]
    Storage["Storage<br/>CSV / SQLite / cloud DB"]
    UI["User Interface<br/>Dashboard / report"]

    Hardware --> Communication --> Processing --> Model
    Model --> Storage --> UI
    Processing --> Storage
```

---

## Slide 10: Advantages of the Proposed System

**Key message:** The proposed system improves maintenance decisions by adding early detection, evidence, and scalability.

**Bullet content:**

- Detects abnormal vibration before major failure
- Reduces unplanned downtime and emergency repair risk
- Supports condition-based maintenance instead of fixed schedules
- Provides historical trend evidence
- Can extend to more sensors, machines, and fault classes

**Speaker notes:**

Compare against existing methods. The main benefit is not only automation; it is better timing. Maintenance can happen when the machine condition indicates risk, not only after failure or at fixed intervals.

**Suggested diagram or visual:**

Outcome comparison between current methods and proposed system.

**Mermaid code:**

```mermaid
flowchart LR
    Current["Current methods<br/>late, manual, fixed, or threshold-based"]
    Proposed["Proposed system<br/>sensor + ML + alerts + trends"]

    Current --> C1["Higher downtime risk"]
    Current --> C2["Unnecessary maintenance"]
    Current --> C3["Limited historical insight"]

    Proposed --> P1["Early warning"]
    Proposed --> P2["Condition-based action"]
    Proposed --> P3["Evidence-driven planning"]
    Proposed --> P4["Scalable architecture"]
```

---

## Slide 11: Challenges, Limitations, and Future Enhancements

**Key message:** The prototype is valuable today, and its limitations define a clear research and development roadmap.

**Bullet content:**

- Challenges: noisy sensor data, sensor placement, limited labeled fault data
- Limitations: academic prototype, not full industrial deployment
- Accuracy depends on data quality and model validation
- Future: fault classification, severity improvement, mobile alerts
- Future: multi-machine monitoring, cloud IoT, remaining useful life estimation

**Speaker notes:**

Be realistic and credible. Acknowledge that predictive maintenance quality depends on good data and testing. Then show that the architecture already supports future growth, including better models, multiple machines, and richer alert channels.

**Suggested diagram or visual:**

Roadmap from current prototype to advanced predictive maintenance platform.

**Mermaid code:**

```mermaid
timeline
    title Future Enhancement Roadmap
    Prototype : Sensor or dataset input
              : Basic health classification
              : Dashboard / report
    Improved Model : Fault type classification
                   : Confidence and severity tuning
                   : More labeled data
    Scaled System : Multi-machine monitoring
                  : Cloud IoT integration
                  : Mobile notifications
    Advanced Research : Remaining useful life estimation
                      : Maintenance scheduling integration
                      : Industrial-grade validation
```

---

## Slide 12: Conclusion

**Key message:** The project demonstrates a complete predictive maintenance loop from vibration sensing to maintenance decision support.

**Bullet content:**

- Built around a real industrial problem: unexpected machine failure
- Uses vibration data as an early warning signal
- Combines IoT, signal processing, ML, dashboard, and alerts
- Supports better maintenance timing and reduced downtime
- Provides a foundation for future industrial and research extensions

**Speaker notes:**

Close by summarizing the contribution. The project shows how sensor data can be converted into machine health insight and actionable alerts. Reinforce that the system assists human maintenance decisions and provides a scalable base for future improvements.

**Suggested diagram or visual:**

Final contribution map connecting technical components to outcomes.

**Mermaid code:**

```mermaid
flowchart LR
    Sensing["Vibration sensing"] --> Intelligence["Signal processing + ML"]
    Intelligence --> Action["Severity alerts + recommendations"]
    Action --> Outcome["Reduced downtime<br/>better maintenance planning"]
```

---

## 10-15 Minute Delivery Plan

| Slide | Time |
| --- | --- |
| 1 | 45 sec |
| 2 | 1 min |
| 3 | 1 min |
| 4 | 1.5 min |
| 5 | 1.5 min |
| 6 | 1 min |
| 7 | 1 min |
| 8 | 1.5 min |
| 9 | 1 min |
| 10 | 1 min |
| 11 | 1 min |
| 12 | 45 sec |

