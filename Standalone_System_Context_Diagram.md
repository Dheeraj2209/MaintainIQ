# Standalone System Context Diagram

## Smart Predictive Maintenance System Using Vibration Data

This standalone diagram shows the complete system in one view: external actors, monitored machine environment, internal system components, data stores, and output channels.

## Mermaid

```mermaid
flowchart LR
    subgraph actors["External Actors"]
        operator["Maintenance Operator"]
        supervisor["Supervisor / Professor"]
        developer["Student Developer"]
    end

    subgraph field["Machine Environment"]
        machine["Machine Under Monitoring"]
        sensor["Vibration Sensor / Accelerometer"]
        gateway["Microcontroller / IoT Gateway"]
        machine -->|"mechanical vibration"| sensor
        sensor -->|"raw acceleration readings"| gateway
    end

    subgraph system["Smart Predictive Maintenance System"]
        ingestion["Data Ingestion Module"]
        preprocessing["Preprocessing Module"]
        features["Feature Extraction Module"]
        prediction["ML / Rule-Based Prediction Module"]
        severity["Severity and Recommendation Module"]
        alerting["Alert Management Module"]
        dashboard["Dashboard / Report Interface"]

        ingestion -->|"validated readings"| preprocessing
        preprocessing -->|"cleaned signal windows"| features
        features -->|"RMS, peak, mean, std, optional FFT"| prediction
        prediction -->|"healthy, degrading, faulty + confidence"| severity
        severity -->|"abnormal condition"| alerting
        severity -->|"current status"| dashboard
        alerting -->|"alert status"| dashboard
    end

    subgraph stores["System Data Stores"]
        rawStore[("Raw Vibration History")]
        featureStore[("Feature and Prediction Results")]
        alertStore[("Alert and Acknowledgement Log")]
    end

    subgraph channels["Output Channels"]
        notification["Dashboard / Email / SMS / Mobile Alert"]
        report["Historical Trend Report"]
    end

    gateway -->|"Serial / Wi-Fi / MQTT / HTTP / Bluetooth"| ingestion
    developer -->|"configure sensor, dataset, model"| ingestion
    ingestion --> rawStore
    features --> featureStore
    severity --> featureStore
    alerting --> alertStore
    featureStore --> dashboard
    alertStore --> dashboard
    dashboard --> report
    alerting --> notification

    operator -->|"view status and acknowledge alerts"| dashboard
    supervisor -->|"review trends and maintenance priority"| dashboard
    notification --> operator
    report --> supervisor
```

## PlantUML

```plantuml
@startuml
title Smart Predictive Maintenance System - Standalone Context Diagram
skinparam componentStyle rectangle
skinparam shadowing false

package "External Actors" {
  actor "Maintenance Operator" as Operator
  actor "Supervisor / Professor" as Supervisor
  actor "Student Developer" as Developer
}

package "Machine Environment" {
  node "Machine Under Monitoring" as Machine
  component "Vibration Sensor /\nAccelerometer" as Sensor
  node "Microcontroller /\nIoT Gateway" as Gateway
  Machine --> Sensor : mechanical vibration
  Sensor --> Gateway : raw acceleration readings
}

package "Smart Predictive Maintenance System" {
  component "Data Ingestion Module" as Ingestion
  component "Preprocessing Module" as Preprocessing
  component "Feature Extraction Module" as Features
  component "ML / Rule-Based\nPrediction Module" as Prediction
  component "Severity and\nRecommendation Module" as Severity
  component "Alert Management Module" as Alerting
  component "Dashboard /\nReport Interface" as Dashboard

  Ingestion --> Preprocessing : validated readings
  Preprocessing --> Features : cleaned signal windows
  Features --> Prediction : RMS, peak, mean, std,\noptional FFT
  Prediction --> Severity : healthy, degrading, faulty\n+ confidence
  Severity --> Alerting : abnormal condition
  Severity --> Dashboard : current status
  Alerting --> Dashboard : alert status
}

package "System Data Stores" {
  database "Raw Vibration History" as RawStore
  database "Feature and\nPrediction Results" as FeatureStore
  database "Alert and\nAcknowledgement Log" as AlertStore
}

package "Output Channels" {
  component "Dashboard / Email /\nSMS / Mobile Alert" as Notification
  component "Historical Trend Report" as Report
}

Gateway --> Ingestion : Serial / Wi-Fi /\nMQTT / HTTP / Bluetooth
Developer --> Ingestion : configure sensor,\ndataset, model
Ingestion --> RawStore
Features --> FeatureStore
Severity --> FeatureStore
Alerting --> AlertStore
FeatureStore --> Dashboard
AlertStore --> Dashboard
Dashboard --> Report
Alerting --> Notification

Operator --> Dashboard : view status and\nacknowledge alerts
Supervisor --> Dashboard : review trends and\nmaintenance priority
Notification --> Operator
Report --> Supervisor
@enduml
```

## How To Use This Diagram

- Use it as a single architecture overview slide.
- Place it after the problem statement and before detailed data flow or sequence diagrams.
- Explain it from left to right: actors and machine environment feed the system, the system analyzes vibration, data stores preserve evidence, and output channels help users act.

