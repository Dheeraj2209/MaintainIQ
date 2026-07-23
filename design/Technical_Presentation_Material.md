# Technical Presentation Material

## Smart Predictive Maintenance System Using Vibration Data

### Repository Analysis Summary

This repository is a documentation-first project for an academic prototype. It does not contain application source code yet; the architecture is derived from the current project documents:

- `PROJECT_CONTEXT.md`: defines the core idea, basic project flow, value, and advanced feature direction.
- `SRS_Document.md`: defines functional requirements, non-functional requirements, interfaces, data requirements, use cases, architecture layers, and risks.
- `Existing_vs_Proposed_System.md`: compares reactive, scheduled, manual, threshold-based, and advanced industrial systems against the proposed IoT and ML-based approach.
- `SRS_Document.docx` and `Existing_vs_Proposed_System.docx`: Word companions to the Markdown source documents.

The proposed system is a smart predictive maintenance prototype for machines such as motors, pumps, fans, and bearings. It captures vibration signals through a sensor or dataset, sends readings to a local or cloud processing system, preprocesses the signal, extracts time-domain and optional frequency-domain features, classifies machine health, stores results, and raises alerts with severity and recommendations.

### Architectural Assumptions

- The current scope is an academic proof of concept, not a full industrial deployment.
- Data can come from either physical vibration hardware or a stored/simulated dataset.
- Processing can run locally on a laptop or on a cloud/server environment.
- Communication protocols may include Serial, Wi-Fi, MQTT, HTTP, Bluetooth, or similar options depending on the prototype hardware.
- Storage may be CSV, SQLite, another lightweight database, or cloud storage.
- The dashboard may be a web dashboard, desktop UI, or structured report.
- Security controls apply most strongly when cloud communication, multiple users, or remote dashboards are enabled.

### Critical Workflow Ranking

The top 3 critical workflows selected for sequence diagrams are:

1. **Continuous monitoring and health classification**: This is the core system loop and covers collection, preprocessing, feature extraction, prediction, and dashboard update.
2. **Abnormal vibration alert lifecycle**: This turns model output into operational action and addresses severity, duplicate suppression, notification, acknowledgement, and recommendation.
3. **Historical trend review**: This supports supervisor decision-making, validates the value of stored data, and helps show gradual degradation over time.

---

## 1. High-Level System Architecture Diagram

### Reasoning

This diagram shows the system as a layered IoT and ML architecture. It mirrors the SRS architecture layers: machine, sensor, data acquisition, processing, ML, storage, and application. It also includes the dataset path because the SRS explicitly allows simulated or stored vibration data for academic demonstration. The alert service is separated from the ML model because the model predicts condition, while alerting translates prediction into user-facing action.

### Mermaid

```mermaid
flowchart LR
    subgraph Field["Machine and sensing layer"]
        Machine["Physical machine<br/>(motor, pump, fan, bearing)"]
        Sensor["Vibration sensor / accelerometer"]
        Machine -->|"mechanical vibration"| Sensor
    end

    subgraph Edge["Data acquisition layer"]
        Gateway["Microcontroller / gateway<br/>(Arduino, ESP32, Raspberry Pi)"]
        Buffer["Local buffer<br/>(temporary failure handling)"]
        Sensor -->|"raw vibration readings"| Gateway
        Gateway --> Buffer
    end

    subgraph Transport["Communication layer"]
        Protocol["Serial / Wi-Fi / MQTT / HTTP / Bluetooth"]
        Gateway -->|"timestamped readings"| Protocol
    end

    subgraph Processing["Processing and intelligence layer"]
        Ingestion["Data ingestion service"]
        Preprocess["Preprocessing<br/>(clean, normalize, window)"]
        Features["Feature extraction<br/>(mean, std, peak, RMS, optional FFT)"]
        Model["ML or rule model<br/>(healthy, degrading, faulty)"]
        Severity["Severity and recommendation logic"]
        Ingestion --> Preprocess --> Features --> Model --> Severity
    end

    Dataset["Stored / simulated vibration dataset"] --> Ingestion
    Protocol --> Ingestion

    subgraph Storage["Storage layer"]
        RawStore[("Raw and processed vibration history")]
        ResultStore[("Prediction results and health status")]
        AlertStore[("Alert log and acknowledgement history")]
    end

    Ingestion --> RawStore
    Features --> ResultStore
    Severity --> ResultStore

    subgraph Application["Application layer"]
        Dashboard["Dashboard / report UI"]
        AlertService["Alert service<br/>(dashboard, email, SMS, mobile optional)"]
    end

    Severity -->|"abnormal condition"| AlertService
    AlertService --> AlertStore
    ResultStore --> Dashboard
    AlertStore --> Dashboard

    Operator["Maintenance operator"]
    Supervisor["Supervisor / evaluator"]
    Dashboard --> Operator
    Dashboard --> Supervisor
    AlertService --> Operator
```

### PlantUML

```plantuml
@startuml
title High-Level System Architecture - Smart Predictive Maintenance
skinparam componentStyle rectangle
skinparam shadowing false

package "Machine and sensing layer" {
  node "Physical machine\n(motor, pump, fan, bearing)" as Machine
  component "Vibration sensor /\naccelerometer" as Sensor
  Machine --> Sensor : mechanical vibration
}

package "Data acquisition layer" {
  node "Microcontroller / gateway\n(Arduino, ESP32, Raspberry Pi)" as Gateway
  database "Local buffer\n(temporary failure handling)" as Buffer
  Sensor --> Gateway : raw vibration readings
  Gateway --> Buffer
}

package "Communication layer" {
  component "Serial / Wi-Fi /\nMQTT / HTTP / Bluetooth" as Protocol
  Gateway --> Protocol : timestamped readings
}

package "Processing and intelligence layer" {
  component "Data ingestion service" as Ingestion
  component "Preprocessing\n(clean, normalize, window)" as Preprocess
  component "Feature extraction\n(mean, std, peak, RMS, optional FFT)" as Features
  component "ML or rule model\n(healthy, degrading, faulty)" as Model
  component "Severity and recommendation logic" as Severity
  Ingestion --> Preprocess
  Preprocess --> Features
  Features --> Model
  Model --> Severity
}

database "Stored / simulated\nvibration dataset" as Dataset
Dataset --> Ingestion
Protocol --> Ingestion

package "Storage layer" {
  database "Raw and processed\nvibration history" as RawStore
  database "Prediction results\nand health status" as ResultStore
  database "Alert log and\nacknowledgement history" as AlertStore
}

Ingestion --> RawStore
Features --> ResultStore
Severity --> ResultStore

package "Application layer" {
  component "Dashboard / report UI" as Dashboard
  component "Alert service\n(dashboard, email, SMS, mobile optional)" as AlertService
}

Severity --> AlertService : abnormal condition
AlertService --> AlertStore
ResultStore --> Dashboard
AlertStore --> Dashboard

actor "Maintenance operator" as Operator
actor "Supervisor / evaluator" as Supervisor
Dashboard --> Operator
Dashboard --> Supervisor
AlertService --> Operator
@enduml
```

---

## 2. Component-Level Architecture Diagram

### Reasoning

This diagram expands the modules listed in the SRS into implementable components. The boundary between edge acquisition, signal processing, application services, and storage makes the system modular, satisfying the maintainability requirement that collection, preprocessing, model analysis, alerting, and dashboard functions should be separated. The model artifact is shown as a deployable asset so future versions can improve the classifier without changing the rest of the pipeline.

### Mermaid

```mermaid
flowchart TB
    subgraph Edge["Edge acquisition components"]
        SensorAdapter["Sensor adapter<br/>reads accelerometer values"]
        Timestamping["Timestamp and machine ID tagging"]
        EdgeBuffer["Local buffer / retry queue"]
        TransmissionClient["Transmission client<br/>Serial, MQTT, HTTP, Bluetooth"]
        SensorAdapter --> Timestamping --> EdgeBuffer --> TransmissionClient
    end

    subgraph Pipeline["Signal-processing and prediction pipeline"]
        IngestionAPI["Ingestion API / file loader"]
        Validator["Reading validator<br/>missing, invalid, noisy data"]
        Windowing["Windowing and normalization"]
        FeatureExtractor["Feature extractor<br/>mean, std, peak, RMS, optional FFT"]
        Classifier["Health classifier<br/>healthy, degrading, faulty"]
        SeverityScorer["Severity scorer"]
        RecommendationEngine["Maintenance recommendation engine"]
        IngestionAPI --> Validator --> Windowing --> FeatureExtractor --> Classifier --> SeverityScorer --> RecommendationEngine
    end

    subgraph ModelOps["Model assets"]
        Dataset["Training / simulated datasets"]
        ModelArtifact["Trained model or rule configuration"]
        Evaluation["Accuracy and limitation notes"]
        Dataset --> Evaluation --> ModelArtifact
        ModelArtifact --> Classifier
    end

    subgraph AppServices["Application services"]
        AlertManager["Alert manager"]
        DuplicateGuard["Duplicate alert suppression"]
        TrendService["Trend query service"]
        DashboardAPI["Dashboard API"]
        NotificationAdapter["Notification adapter<br/>dashboard, email, SMS, mobile optional"]
        AlertManager --> DuplicateGuard --> NotificationAdapter
        TrendService --> DashboardAPI
    end

    subgraph Stores["Storage components"]
        RawData[("Raw vibration records")]
        ProcessedData[("Processed windows and features")]
        PredictionData[("Health status, severity, confidence")]
        AlertLog[("Alert and acknowledgement log")]
    end

    subgraph UI["User-facing components"]
        Dashboard["Dashboard / report"]
        Operator["Maintenance operator"]
        Supervisor["Supervisor / evaluator"]
        Dashboard --> Operator
        Dashboard --> Supervisor
    end

    TransmissionClient --> IngestionAPI
    IngestionAPI --> RawData
    FeatureExtractor --> ProcessedData
    SeverityScorer --> PredictionData
    RecommendationEngine --> AlertManager
    AlertManager --> AlertLog
    PredictionData --> TrendService
    ProcessedData --> TrendService
    AlertLog --> TrendService
    NotificationAdapter --> Dashboard
    DashboardAPI --> Dashboard
```

### PlantUML

```plantuml
@startuml
title Component-Level Architecture
skinparam componentStyle rectangle
skinparam shadowing false

package "Edge acquisition components" {
  component "Sensor adapter\nreads accelerometer values" as SensorAdapter
  component "Timestamp and\nmachine ID tagging" as Timestamping
  queue "Local buffer /\nretry queue" as EdgeBuffer
  component "Transmission client\nSerial, MQTT, HTTP, Bluetooth" as TransmissionClient
  SensorAdapter --> Timestamping
  Timestamping --> EdgeBuffer
  EdgeBuffer --> TransmissionClient
}

package "Signal-processing and prediction pipeline" {
  component "Ingestion API /\nfile loader" as IngestionAPI
  component "Reading validator\nmissing, invalid, noisy data" as Validator
  component "Windowing and\nnormalization" as Windowing
  component "Feature extractor\nmean, std, peak, RMS, optional FFT" as FeatureExtractor
  component "Health classifier\nhealthy, degrading, faulty" as Classifier
  component "Severity scorer" as SeverityScorer
  component "Maintenance\nrecommendation engine" as RecommendationEngine
  IngestionAPI --> Validator
  Validator --> Windowing
  Windowing --> FeatureExtractor
  FeatureExtractor --> Classifier
  Classifier --> SeverityScorer
  SeverityScorer --> RecommendationEngine
}

package "Model assets" {
  database "Training / simulated datasets" as Dataset
  artifact "Trained model or\nrule configuration" as ModelArtifact
  component "Accuracy and\nlimitation notes" as Evaluation
  Dataset --> Evaluation
  Evaluation --> ModelArtifact
  ModelArtifact --> Classifier
}

package "Application services" {
  component "Alert manager" as AlertManager
  component "Duplicate alert\nsuppression" as DuplicateGuard
  component "Trend query service" as TrendService
  component "Dashboard API" as DashboardAPI
  component "Notification adapter\ndashboard, email, SMS, mobile optional" as NotificationAdapter
  AlertManager --> DuplicateGuard
  DuplicateGuard --> NotificationAdapter
  TrendService --> DashboardAPI
}

package "Storage components" {
  database "Raw vibration records" as RawData
  database "Processed windows\nand features" as ProcessedData
  database "Health status, severity,\nconfidence" as PredictionData
  database "Alert and\nacknowledgement log" as AlertLog
}

package "User-facing components" {
  component "Dashboard / report" as Dashboard
  actor "Maintenance operator" as Operator
  actor "Supervisor / evaluator" as Supervisor
  Dashboard --> Operator
  Dashboard --> Supervisor
}

TransmissionClient --> IngestionAPI
IngestionAPI --> RawData
FeatureExtractor --> ProcessedData
SeverityScorer --> PredictionData
RecommendationEngine --> AlertManager
AlertManager --> AlertLog
PredictionData --> TrendService
ProcessedData --> TrendService
AlertLog --> TrendService
NotificationAdapter --> Dashboard
DashboardAPI --> Dashboard
@enduml
```

---

## 3. User Journey Diagram

### Reasoning

This diagram frames the system from the perspective of operators, supervisors, and student developers. It connects setup, monitoring, alert response, and review. The user journey is important for the presentation because the project is not only a model pipeline; its value comes from turning vibration changes into a maintenance decision before a breakdown.

### Mermaid

```mermaid
journey
    title Predictive Maintenance User Journey
    section Setup
      Attach sensor to target machine: 3: Student Developer
      Configure machine ID and sampling method: 3: Student Developer
      Start data collection or load dataset: 4: Student Developer
    section Monitoring
      Collect vibration readings continuously or periodically: 5: System
      Analyze machine health state: 5: System
      Display current status on dashboard: 4: Maintenance Operator
    section Abnormal Condition
      Detect degrading or faulty pattern: 5: System
      Generate alert with severity and issue description: 5: System
      Review alert and inspect machine: 4: Maintenance Operator
    section Decision
      Use recommendation to schedule maintenance: 4: Supervisor
      Confirm repair or continued observation: 4: Maintenance Operator
    section Learning and Improvement
      Review historical vibration trends: 4: Supervisor
      Add more labeled samples for better accuracy: 3: Student Developer
```

### PlantUML

```plantuml
@startuml
title User Journey - Predictive Maintenance
skinparam shadowing false

|Student Developer|
start
:Attach sensor to target machine;
:Configure machine ID and sampling method;
:Start data collection or load dataset;

|System|
:Collect vibration readings continuously or periodically;
:Analyze machine health state;

|Maintenance Operator|
:View current machine status on dashboard;

|System|
if (Degrading or faulty pattern detected?) then (yes)
  :Generate alert with severity and issue description;
  |Maintenance Operator|
  :Review alert and inspect machine;
  |Supervisor|
  :Use recommendation to schedule maintenance;
  |Maintenance Operator|
  :Confirm repair or continue observation;
else (no)
  |Maintenance Operator|
  :Continue monitoring;
endif

|Supervisor|
:Review historical vibration trends;

|Student Developer|
:Add more labeled samples for better accuracy;
stop
@enduml
```

---

## 4. Sequence Diagram 1 - Continuous Monitoring and Health Classification

### Reasoning

This is the core runtime loop. It maps directly to the functional requirements for data collection, transmission, preprocessing, feature extraction, classification, storage, and dashboard display. The sequence includes the communication failure fallback because reliability is a stated requirement and the SRS expects temporary failures to be handled by notification or local storage where possible.

### Mermaid

```mermaid
sequenceDiagram
    autonumber
    participant M as Machine
    participant S as Vibration Sensor
    participant G as Microcontroller / Gateway
    participant I as Ingestion Service
    participant P as Preprocessing Module
    participant F as Feature Extraction Module
    participant ML as Prediction Model
    participant DB as Storage
    participant UI as Dashboard

    loop Continuous or periodic sampling
        M->>S: Produce vibration signal
        S->>G: Send raw acceleration reading
        G->>G: Add timestamp, machine ID, sensor ID
        alt Communication available
            G->>I: Transmit reading batch
        else Temporary communication failure
            G->>G: Store readings in local retry buffer
            G-->>UI: Optional connectivity warning
        end
        I->>DB: Store raw reading
        I->>P: Forward reading window
        P->>P: Clean, normalize, window signal
        P->>F: Send prepared window
        F->>F: Calculate RMS, peak, mean, std, optional FFT
        F->>ML: Submit feature vector
        ML-->>F: Return health class, confidence, severity basis
        F->>DB: Store features and prediction result
        DB-->>UI: Provide latest status and trend data
        UI-->>UI: Refresh machine health display
    end
```

### PlantUML

```plantuml
@startuml
title Sequence 1 - Continuous Monitoring and Health Classification
autonumber

participant "Machine" as M
participant "Vibration Sensor" as S
participant "Microcontroller /\nGateway" as G
participant "Ingestion Service" as I
participant "Preprocessing Module" as P
participant "Feature Extraction Module" as F
participant "Prediction Model" as ML
database "Storage" as DB
participant "Dashboard" as UI

loop Continuous or periodic sampling
  M -> S : Produce vibration signal
  S -> G : Send raw acceleration reading
  G -> G : Add timestamp, machine ID, sensor ID
  alt Communication available
    G -> I : Transmit reading batch
  else Temporary communication failure
    G -> G : Store readings in local retry buffer
    G --> UI : Optional connectivity warning
  end
  I -> DB : Store raw reading
  I -> P : Forward reading window
  P -> P : Clean, normalize, window signal
  P -> F : Send prepared window
  F -> F : Calculate RMS, peak, mean, std, optional FFT
  F -> ML : Submit feature vector
  ML --> F : Return health class, confidence, severity basis
  F -> DB : Store features and prediction result
  DB --> UI : Provide latest status and trend data
  UI -> UI : Refresh machine health display
end
@enduml
```

---

## 5. Sequence Diagram 2 - Abnormal Vibration Alert Lifecycle

### Reasoning

This workflow starts after classification identifies a degrading or faulty condition. It shows how a technical prediction becomes a human action. Duplicate suppression is included because repeated alerts are a risk in the SRS. The alert includes health status, time, severity, issue description, and recommendation, matching the alert requirements.

### Mermaid

```mermaid
sequenceDiagram
    autonumber
    participant ML as Prediction Model
    participant SS as Severity Scorer
    participant AM as Alert Manager
    participant DG as Duplicate Guard
    participant AL as Alert Log
    participant NA as Notification Adapter
    participant UI as Dashboard
    actor OP as Maintenance Operator

    ML->>SS: Return abnormal class or high-risk score
    SS->>SS: Calculate severity and recommendation
    SS->>AM: Request alert creation
    AM->>DG: Check unresolved alerts for same machine and fault
    alt Duplicate unresolved alert exists
        DG-->>AM: Suppress duplicate or update existing alert count
        AM->>AL: Update alert history and latest timestamp
        AL-->>UI: Show existing alert with updated context
    else New actionable alert
        DG-->>AM: Allow new alert
        AM->>AL: Store alert with severity, time, status, issue description
        AM->>NA: Send notification request
        NA-->>UI: Push dashboard notification
        NA-->>OP: Optional email, SMS, or mobile notification
        OP->>UI: Open alert details
        UI->>AL: Record acknowledgement
        OP->>OP: Inspect machine and plan maintenance
    end
```

### PlantUML

```plantuml
@startuml
title Sequence 2 - Abnormal Vibration Alert Lifecycle
autonumber

participant "Prediction Model" as ML
participant "Severity Scorer" as SS
participant "Alert Manager" as AM
participant "Duplicate Guard" as DG
database "Alert Log" as AL
participant "Notification Adapter" as NA
participant "Dashboard" as UI
actor "Maintenance Operator" as OP

ML -> SS : Return abnormal class or high-risk score
SS -> SS : Calculate severity and recommendation
SS -> AM : Request alert creation
AM -> DG : Check unresolved alerts for same machine and fault

alt Duplicate unresolved alert exists
  DG --> AM : Suppress duplicate or update existing alert count
  AM -> AL : Update alert history and latest timestamp
  AL --> UI : Show existing alert with updated context
else New actionable alert
  DG --> AM : Allow new alert
  AM -> AL : Store alert with severity, time, status, issue description
  AM -> NA : Send notification request
  NA --> UI : Push dashboard notification
  NA --> OP : Optional email, SMS, or mobile notification
  OP -> UI : Open alert details
  UI -> AL : Record acknowledgement
  OP -> OP : Inspect machine and plan maintenance
end
@enduml
```

---

## 6. Sequence Diagram 3 - Historical Trend Review

### Reasoning

Historical review is the bridge between raw monitoring and maintenance planning. It shows the supervisor asking for trends, the system retrieving vibration windows, predictions, and alert logs, and the dashboard presenting evidence. This workflow supports the SRS requirements for historical vibration trends, alert history, and retrieval of past machine health data.

### Mermaid

```mermaid
sequenceDiagram
    autonumber
    actor SUP as Supervisor
    participant UI as Dashboard
    participant AUTH as Access Control
    participant TS as Trend Query Service
    participant VR as Vibration Store
    participant PR as Prediction Store
    participant AL as Alert Log

    SUP->>UI: Select machine and time range
    UI->>AUTH: Validate dashboard access
    alt Authorized user
        AUTH-->>UI: Access allowed
        UI->>TS: Request trend package
        TS->>VR: Fetch vibration readings / features
        TS->>PR: Fetch health states, confidence, severity
        TS->>AL: Fetch abnormal events and acknowledgements
        VR-->>TS: Vibration history
        PR-->>TS: Prediction timeline
        AL-->>TS: Alert history
        TS-->>UI: Return trend graph data and summary
        UI-->>SUP: Display vibration trend, status changes, alerts
        SUP->>SUP: Decide maintenance priority or continued observation
    else Unauthorized user
        AUTH-->>UI: Deny access
        UI-->>SUP: Show access-limited message
    end
```

### PlantUML

```plantuml
@startuml
title Sequence 3 - Historical Trend Review
autonumber

actor "Supervisor" as SUP
participant "Dashboard" as UI
participant "Access Control" as AUTH
participant "Trend Query Service" as TS
database "Vibration Store" as VR
database "Prediction Store" as PR
database "Alert Log" as AL

SUP -> UI : Select machine and time range
UI -> AUTH : Validate dashboard access

alt Authorized user
  AUTH --> UI : Access allowed
  UI -> TS : Request trend package
  TS -> VR : Fetch vibration readings / features
  TS -> PR : Fetch health states, confidence, severity
  TS -> AL : Fetch abnormal events and acknowledgements
  VR --> TS : Vibration history
  PR --> TS : Prediction timeline
  AL --> TS : Alert history
  TS --> UI : Return trend graph data and summary
  UI --> SUP : Display vibration trend, status changes, alerts
  SUP -> SUP : Decide maintenance priority or continued observation
else Unauthorized user
  AUTH --> UI : Deny access
  UI --> SUP : Show access-limited message
end
@enduml
```

---

## 7. Current Industry Approach vs Our Approach Comparison Diagram

### Reasoning

The comparison document identifies five existing approaches: reactive, scheduled, manual inspection, threshold-based monitoring, and advanced industrial systems. This diagram contrasts those methods with the proposed system's predictive, continuous, data-driven approach. It is designed for a business-value slide because it makes the shift clear: from late or fixed-time maintenance to early warning based on actual machine condition.

### Mermaid

```mermaid
flowchart LR
    subgraph Current["Current industry approaches"]
        Reactive["Reactive maintenance<br/>repair after failure"]
        Scheduled["Scheduled maintenance<br/>fixed interval service"]
        Manual["Manual inspection<br/>technician-dependent checks"]
        Threshold["Threshold monitoring<br/>fixed limit alarms"]
        Enterprise["Advanced industrial monitoring<br/>powerful but costly and complex"]
    end

    subgraph Gaps["Common limitations"]
        Late["Faults detected late"]
        Waste["Unnecessary maintenance possible"]
        Inconsistent["Human judgment varies"]
        FalseAlarms["Threshold tuning causes misses or false alarms"]
        CostBarrier["High setup cost for small teams"]
    end

    subgraph Proposed["Proposed predictive maintenance approach"]
        SensorData["Continuous or periodic vibration sensing"]
        SignalFeatures["Signal preprocessing and feature extraction"]
        MLDecision["ML or rule-based health classification"]
        Severity["Severity scoring and recommendation"]
        Trends["Historical trend analysis"]
        SmartAlerts["Actionable alerts before major failure"]
    end

    Reactive --> Late
    Scheduled --> Waste
    Manual --> Inconsistent
    Threshold --> FalseAlarms
    Enterprise --> CostBarrier

    Late --> SensorData
    Waste --> SensorData
    Inconsistent --> SignalFeatures
    FalseAlarms --> MLDecision
    CostBarrier --> SensorData

    SensorData --> SignalFeatures --> MLDecision --> Severity --> SmartAlerts
    Severity --> Trends
    Trends --> SmartAlerts
```

### PlantUML

```plantuml
@startuml
title Current Industry Approach vs Proposed Approach
skinparam componentStyle rectangle
skinparam shadowing false

package "Current industry approaches" {
  component "Reactive maintenance\nrepair after failure" as Reactive
  component "Scheduled maintenance\nfixed interval service" as Scheduled
  component "Manual inspection\ntechnician-dependent checks" as Manual
  component "Threshold monitoring\nfixed limit alarms" as Threshold
  component "Advanced industrial monitoring\npowerful but costly and complex" as Enterprise
}

package "Common limitations" {
  component "Faults detected late" as Late
  component "Unnecessary maintenance possible" as Waste
  component "Human judgment varies" as Inconsistent
  component "Threshold tuning causes\nmisses or false alarms" as FalseAlarms
  component "High setup cost for\nsmall teams" as CostBarrier
}

package "Proposed predictive maintenance approach" {
  component "Continuous or periodic\nvibration sensing" as SensorData
  component "Signal preprocessing and\nfeature extraction" as SignalFeatures
  component "ML or rule-based\nhealth classification" as MLDecision
  component "Severity scoring and\nrecommendation" as Severity
  component "Historical trend analysis" as Trends
  component "Actionable alerts before\nmajor failure" as SmartAlerts
}

Reactive --> Late
Scheduled --> Waste
Manual --> Inconsistent
Threshold --> FalseAlarms
Enterprise --> CostBarrier

Late --> SensorData
Waste --> SensorData
Inconsistent --> SignalFeatures
FalseAlarms --> MLDecision
CostBarrier --> SensorData

SensorData --> SignalFeatures
SignalFeatures --> MLDecision
MLDecision --> Severity
Severity --> SmartAlerts
Severity --> Trends
Trends --> SmartAlerts
@enduml
```

---

## 8. Data Flow Diagram

### Reasoning

This is a level-1 data flow view. It focuses on what data enters, how it transforms, and which stores retain it. It highlights the core data products: raw readings, cleaned windows, features, prediction results, alerts, and trend summaries. This diagram is useful for explaining the technical pipeline without showing deployment infrastructure.

### Mermaid

```mermaid
flowchart LR
    ExternalMachine["External entity:<br/>Machine under monitoring"]
    User["External entity:<br/>Operator / Supervisor"]

    P1["P1 Collect vibration data"]
    P2["P2 Transmit or load data"]
    P3["P3 Preprocess signal"]
    P4["P4 Extract features"]
    P5["P5 Predict machine health"]
    P6["P6 Generate alert and recommendation"]
    P7["P7 Present dashboard and reports"]

    D1[("D1 Raw vibration readings")]
    D2[("D2 Processed windows")]
    D3[("D3 Feature vectors")]
    D4[("D4 Prediction results")]
    D5[("D5 Alert history")]

    ExternalMachine -->|"acceleration signal"| P1
    P1 -->|"timestamp, machine ID, sensor ID, sampling rate"| D1
    P1 -->|"reading batch"| P2
    P2 -->|"valid received data"| P3
    D1 -->|"stored readings for replay"| P3
    P3 -->|"cleaned and normalized windows"| D2
    P3 -->|"prepared signal windows"| P4
    P4 -->|"mean, std, peak, RMS, optional FFT"| D3
    P4 -->|"feature vector"| P5
    P5 -->|"health class, confidence, severity basis"| D4
    P5 -->|"abnormal result"| P6
    P6 -->|"alert, severity, issue, recommendation"| D5
    D2 --> P7
    D3 --> P7
    D4 --> P7
    D5 --> P7
    P7 -->|"status, trends, alerts, recommendation"| User
    User -->|"acknowledgement, machine/time filters"| P7
    P7 -->|"acknowledgement update"| D5
```

### PlantUML

```plantuml
@startuml
title Data Flow Diagram - Level 1
skinparam componentStyle rectangle
skinparam shadowing false

actor "Machine under monitoring" as ExternalMachine
actor "Operator / Supervisor" as User

component "P1 Collect\nvibration data" as P1
component "P2 Transmit\nor load data" as P2
component "P3 Preprocess\nsignal" as P3
component "P4 Extract\nfeatures" as P4
component "P5 Predict\nmachine health" as P5
component "P6 Generate alert\nand recommendation" as P6
component "P7 Present dashboard\nand reports" as P7

database "D1 Raw vibration\nreadings" as D1
database "D2 Processed\nwindows" as D2
database "D3 Feature\nvectors" as D3
database "D4 Prediction\nresults" as D4
database "D5 Alert\nhistory" as D5

ExternalMachine --> P1 : acceleration signal
P1 --> D1 : timestamp, machine ID,\nsensor ID, sampling rate
P1 --> P2 : reading batch
P2 --> P3 : valid received data
D1 --> P3 : stored readings for replay
P3 --> D2 : cleaned and normalized windows
P3 --> P4 : prepared signal windows
P4 --> D3 : mean, std, peak, RMS,\noptional FFT
P4 --> P5 : feature vector
P5 --> D4 : health class, confidence,\nseverity basis
P5 --> P6 : abnormal result
P6 --> D5 : alert, severity, issue,\nrecommendation
D2 --> P7
D3 --> P7
D4 --> P7
D5 --> P7
P7 --> User : status, trends, alerts,\nrecommendation
User --> P7 : acknowledgement,\nmachine/time filters
P7 --> D5 : acknowledgement update
@enduml
```

---

## 9. Deployment Architecture Diagram

### Reasoning

The SRS allows three operating modes: local prototype, cloud-connected setup, and simulated dataset demonstration. This diagram shows all three without implying that the academic prototype must use full industrial infrastructure. It separates edge hardware, processing runtime, storage, user devices, and optional external notification channels.

### Mermaid

```mermaid
flowchart TB
    subgraph Site["Machine site / lab setup"]
        Machine["Machine under test"]
        Sensor["Vibration sensor / accelerometer"]
        Gateway["Microcontroller or gateway<br/>Arduino, ESP32, Raspberry Pi"]
        Machine --> Sensor --> Gateway
    end

    subgraph Local["Local prototype option"]
        Laptop["Laptop / local workstation"]
        LocalRuntime["Python processing and ML runtime"]
        LocalDB[("CSV / SQLite / local files")]
        LocalDashboard["Local web dashboard or report"]
        Laptop --> LocalRuntime --> LocalDB
        LocalRuntime --> LocalDashboard
    end

    subgraph Cloud["Cloud-connected option"]
        API["Cloud or server ingestion API"]
        CloudRuntime["Processing service and model runtime"]
        CloudDB[("Cloud database / object storage")]
        WebApp["Remote dashboard web app"]
        API --> CloudRuntime --> CloudDB
        CloudRuntime --> WebApp
    end

    subgraph Simulation["Dataset demonstration option"]
        Dataset["Stored vibration dataset"]
        Dataset --> LocalRuntime
        Dataset --> CloudRuntime
    end

    subgraph Users["User devices"]
        Browser["Operator / supervisor browser"]
        Mobile["Optional mobile or SMS endpoint"]
    end

    Gateway -->|"Serial / USB"| Laptop
    Gateway -->|"Wi-Fi / MQTT / HTTP"| API
    LocalDashboard --> Browser
    WebApp --> Browser
    CloudRuntime -->|"optional alert notification"| Mobile
    LocalRuntime -->|"optional alert notification"| Mobile
```

### PlantUML

```plantuml
@startuml
title Deployment Architecture
skinparam shadowing false

node "Machine site / lab setup" as Site {
  node "Machine under test" as Machine
  device "Vibration sensor /\naccelerometer" as Sensor
  node "Microcontroller or gateway\nArduino, ESP32, Raspberry Pi" as Gateway
  Machine --> Sensor
  Sensor --> Gateway
}

node "Local prototype option" as Local {
  node "Laptop / local workstation" as Laptop
  component "Python processing and\nML runtime" as LocalRuntime
  database "CSV / SQLite /\nlocal files" as LocalDB
  component "Local web dashboard\nor report" as LocalDashboard
  Laptop --> LocalRuntime
  LocalRuntime --> LocalDB
  LocalRuntime --> LocalDashboard
}

cloud "Cloud-connected option" as Cloud {
  component "Cloud or server\ningestion API" as API
  component "Processing service and\nmodel runtime" as CloudRuntime
  database "Cloud database /\nobject storage" as CloudDB
  component "Remote dashboard\nweb app" as WebApp
  API --> CloudRuntime
  CloudRuntime --> CloudDB
  CloudRuntime --> WebApp
}

node "Dataset demonstration option" as Simulation {
  database "Stored vibration dataset" as Dataset
}

node "User devices" as Users {
  node "Operator / supervisor browser" as Browser
  node "Optional mobile or\nSMS endpoint" as Mobile
}

Gateway --> Laptop : Serial / USB
Gateway --> API : Wi-Fi / MQTT / HTTP
Dataset --> LocalRuntime
Dataset --> CloudRuntime
LocalDashboard --> Browser
WebApp --> Browser
CloudRuntime --> Mobile : optional alert notification
LocalRuntime --> Mobile : optional alert notification
@enduml
```

---

## 10. Security Architecture Diagram

### Reasoning

The SRS security requirements are intentionally lightweight because the project is an academic prototype, but they still identify secure communication, dashboard access limits, and avoiding unnecessary exposure of sensitive machine or user data. This diagram turns those requirements into practical security boundaries: physical device access, secure transport, authenticated APIs, role-based dashboard access, protected storage, and auditability.

### Mermaid

```mermaid
flowchart LR
    subgraph Physical["Physical / edge trust zone"]
        Machine["Machine and sensor"]
        Gateway["Gateway firmware"]
        DeviceControls["Controls:<br/>sensor placement, firmware config, local buffer integrity"]
        Machine --> Gateway
        Gateway --> DeviceControls
    end

    subgraph Network["Network boundary"]
        SecureTransport["TLS / HTTPS or MQTT over TLS<br/>where cloud or remote transfer is used"]
        AuthToken["Device API key or token<br/>for ingestion"]
        Gateway --> SecureTransport --> AuthToken
    end

    subgraph Application["Application trust zone"]
        IngestionAPI["Authenticated ingestion API"]
        Validation["Input validation and schema checks"]
        ModelService["Prediction service"]
        DashboardAPI["Dashboard API"]
        RBAC["User authentication and role-based access"]
        Audit["Audit log for alerts and acknowledgements"]
        AuthToken --> IngestionAPI --> Validation --> ModelService
        RBAC --> DashboardAPI
        DashboardAPI --> Audit
    end

    subgraph Data["Data protection zone"]
        VibrationDB[("Vibration and prediction data")]
        AlertDB[("Alert log")]
        Retention["Retention and minimization<br/>avoid unnecessary sensitive data"]
        Backup["Backup / recovery for demonstration data"]
        ModelService --> VibrationDB
        Audit --> AlertDB
        VibrationDB --> Retention
        AlertDB --> Retention
        VibrationDB --> Backup
        AlertDB --> Backup
    end

    Users["Operator / supervisor"] -->|"login"| RBAC
    DashboardAPI -->|"authorized status, trends, alerts"| Users
```

### PlantUML

```plantuml
@startuml
title Security Architecture
skinparam componentStyle rectangle
skinparam shadowing false

package "Physical / edge trust zone" {
  node "Machine and sensor" as Machine
  node "Gateway firmware" as Gateway
  component "Controls:\nsensor placement, firmware config,\nlocal buffer integrity" as DeviceControls
  Machine --> Gateway
  Gateway --> DeviceControls
}

package "Network boundary" {
  component "TLS / HTTPS or MQTT over TLS\nwhere cloud or remote transfer is used" as SecureTransport
  component "Device API key or token\nfor ingestion" as AuthToken
  Gateway --> SecureTransport
  SecureTransport --> AuthToken
}

package "Application trust zone" {
  component "Authenticated ingestion API" as IngestionAPI
  component "Input validation and\nschema checks" as Validation
  component "Prediction service" as ModelService
  component "Dashboard API" as DashboardAPI
  component "User authentication and\nrole-based access" as RBAC
  component "Audit log for alerts\nand acknowledgements" as Audit
  AuthToken --> IngestionAPI
  IngestionAPI --> Validation
  Validation --> ModelService
  RBAC --> DashboardAPI
  DashboardAPI --> Audit
}

package "Data protection zone" {
  database "Vibration and\nprediction data" as VibrationDB
  database "Alert log" as AlertDB
  component "Retention and minimization\navoid unnecessary sensitive data" as Retention
  component "Backup / recovery for\ndemonstration data" as Backup
  ModelService --> VibrationDB
  Audit --> AlertDB
  VibrationDB --> Retention
  AlertDB --> Retention
  VibrationDB --> Backup
  AlertDB --> Backup
}

actor "Operator / supervisor" as Users
Users --> RBAC : login
DashboardAPI --> Users : authorized status,\ntrends, alerts
@enduml
```

---

## 11. Presentation Slide Outline

### Narrative Thesis

The proposed system moves maintenance from reactive or fixed-schedule decisions to condition-based, data-driven decisions by converting vibration patterns into machine health status, severity, alerts, and trend evidence.

### Recommended Deck Structure

| Slide | Claim Title | Proof Object | Business Value | Technical Advantage |
| --- | --- | --- | --- | --- |
| 1 | Vibration data can reveal machine failure before breakdown. | Opening system concept visual: machine, sensor, dashboard, alert. | Positions the project around downtime reduction and early warning. | Anchors the technical story in condition monitoring and signal patterns. |
| 2 | Current maintenance methods either react too late or service too early. | Current industry approach vs proposed approach diagram. | Explains cost, downtime, and resource waste from reactive and scheduled maintenance. | Shows why simple thresholds and manual inspection are insufficient for early-stage faults. |
| 3 | Our approach turns machine vibration into an actionable maintenance decision. | High-level system architecture diagram. | Makes the value chain clear from machine signal to maintenance action. | Shows layered IoT, processing, ML, storage, dashboard, and alerting responsibilities. |
| 4 | The core pipeline is modular, testable, and upgradeable. | Component-level architecture diagram. | Reduces project risk and supports future improvements by module. | Separates acquisition, preprocessing, feature extraction, prediction, alerting, storage, and UI. |
| 5 | The data flow preserves both real-time status and historical evidence. | Data flow diagram. | Enables status monitoring, trend analysis, and maintenance planning. | Shows how raw readings become windows, features, predictions, alerts, and dashboard summaries. |
| 6 | Continuous monitoring provides the earliest warning signal. | Sequence diagram 1: monitoring and health classification. | Reduces unplanned downtime by detecting degradation before failure. | Explains sampling, timestamping, preprocessing, feature extraction, classification, and storage. |
| 7 | Alerts convert prediction into operator action. | Sequence diagram 2: abnormal vibration alert lifecycle. | Prevents alarm fatigue and helps teams act on severity. | Includes severity scoring, recommendation, duplicate suppression, notification, and acknowledgement. |
| 8 | Historical trends help supervisors prioritize maintenance. | Sequence diagram 3: historical trend review. | Moves maintenance decisions from guesswork to evidence. | Combines vibration history, prediction timeline, and alert logs under access control. |
| 9 | The user journey is simple enough for operators, supervisors, and evaluators. | User journey diagram. | Makes the system understandable to non-ML users. | Shows setup, monitoring, alert review, maintenance decision, and future learning loop. |
| 10 | The prototype can run locally, in the cloud, or from a dataset. | Deployment architecture diagram. | Fits academic constraints while leaving room for future scaling. | Supports physical hardware, simulated datasets, local runtime, and cloud-connected deployment. |
| 11 | Security is scoped to the prototype but aligned with real deployment needs. | Security architecture diagram. | Builds evaluator confidence that machine and alert data are not exposed unnecessarily. | Adds secure transport, authenticated ingestion, role-based dashboard access, validation, audit, retention, and backup. |
| 12 | The proposed system improves reliability without replacing human judgement. | Benefits and limitations table. | Shows realistic value: downtime reduction, better scheduling, lower emergency repair risk. | Clarifies model dependency on sensor quality, labeled data, and human verification. |
| 13 | Future enhancements can extend the same architecture. | Roadmap visual: fault classification, RUL, multi-machine monitoring, cloud IoT, mobile alerts, ERP integration. | Demonstrates growth path beyond the prototype. | Shows that the modular design can add sensors, models, fault classes, and integrations. |
| 14 | The final demonstration proves the complete loop from sensor data to maintenance decision. | Demo script timeline. | Gives evaluators a clear success path. | Verifies collection/loading, preprocessing, classification, alerting, dashboard/reporting, and historical review. |

### Speaker Flow

1. Start with the business problem: failures are expensive because current methods often react late or service on fixed schedules.
2. Introduce vibration as the measurable early-warning signal.
3. Show the high-level architecture to make the end-to-end system understandable.
4. Drill down into the component and data flow diagrams to prove technical feasibility.
5. Use the three sequence diagrams to explain exactly how the system behaves during monitoring, alerting, and supervisor review.
6. Close with deployment flexibility, security controls, limitations, and roadmap.

### Key Business Value Points

- Reduces unplanned downtime by detecting abnormal vibration before major failure.
- Reduces unnecessary maintenance by acting on actual machine condition rather than only fixed schedules.
- Helps operators and supervisors make evidence-based maintenance decisions.
- Makes predictive maintenance accessible as an academic prototype using low-cost hardware or datasets.
- Creates a foundation for future multi-machine, cloud, mobile, and industrial integration.

### Key Technical Advantage Points

- Modular pipeline: acquisition, transmission, preprocessing, feature extraction, prediction, alerting, dashboard, and storage are separated.
- Flexible input sources: real sensor readings or simulated/stored vibration datasets.
- Signal-processing foundation: cleaned windows and features such as RMS, peak, mean, standard deviation, and optional FFT.
- Intelligence layer: health classification into healthy, degrading, and faulty states, with optional fault type, confidence, and severity.
- Operational layer: alert generation, duplicate suppression, recommendations, and acknowledgement history.
- Scalability path: future support for multiple machines, new sensors, new fault classes, cloud IoT platforms, and remaining useful life estimation.
- Security-aware design: authenticated transfer, limited dashboard access, validation, audit logs, retention, and backup controls where needed.

### Suggested Visual Order for the Deck

1. Current industry approach vs our approach
2. High-level architecture
3. Component-level architecture
4. Data flow
5. Sequence diagrams for monitoring, alerting, and trend review
6. User journey
7. Deployment architecture
8. Security architecture
9. Roadmap and final demo loop

### Demo Storyline

1. Start the sensor or load a stored vibration dataset.
2. Show raw or recent vibration readings entering the system.
3. Show preprocessing and feature extraction output.
4. Show the classifier result: healthy, degrading, or faulty.
5. Trigger an abnormal case and show severity plus recommendation.
6. Open the dashboard or report to review the current status.
7. Review historical vibration trend and alert log.
8. Explain how the operator uses the alert to inspect or schedule maintenance.
