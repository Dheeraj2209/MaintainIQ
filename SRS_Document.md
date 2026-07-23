# Software Requirements Specification

## Smart Predictive Maintenance Decision-Support System Using Vibration and Temperature Data

### Document Information

| Field | Details |
| --- | --- |
| Project Title | Smart Predictive Maintenance Decision-Support System Using Vibration and Temperature Data |
| Document Type | Software Requirements Specification (SRS) |
| Intended Audience | College professor, project evaluators, developers, and student project team |
| Version | 1.0 |
| Prepared For | Academic project submission |

---

## 1. Introduction

### 1.1 Purpose

The purpose of this document is to define the software requirements for a Smart Predictive Maintenance Decision-Support System that uses vibration and temperature data to monitor machine health, predict possible failures, identify probable root causes, and support maintenance planning before breakdowns occur.

Machines such as motors, pumps, fans, and bearings usually show small changes in vibration patterns and thermal behavior before a major fault or breakdown happens. The proposed system captures vibration and temperature readings using sensors, processes the data through an edge-cloud architecture, and applies machine learning or rule-based techniques to identify whether a machine is healthy, degrading, faulty, or critical.

This SRS is intended to guide the development of the project and provide a clear understanding of the expected features, system behavior, constraints, and quality requirements.

### 1.2 Scope

The proposed system focuses on multi-sensor predictive maintenance using vibration and temperature based condition monitoring. It will collect sensor readings from machines, analyze the combined readings, detect abnormal behavior, identify probable root causes, track maintenance history, and notify users when maintenance attention is required.

The system is intended as an academic prototype and proof of concept. It may be demonstrated using a small machine, motor, fan, bearing setup, or a simulated vibration and temperature dataset if physical hardware is not fully available.

The system will support:

- Vibration and temperature data collection from sensors or datasets.
- Timestamping, machine identification, and temporary edge buffering.
- Preprocessing of vibration and temperature readings.
- Machine health classification into healthy, degrading, faulty, or critical states.
- Abnormal condition detection using multi-sensor evidence.
- Probable root cause identification for detected abnormal conditions.
- Alert generation for possible faults.
- Maintenance history tracking, including last maintenance date and days since maintenance.
- KPI tracking for machine health, maintenance response, prediction quality, system performance, and operational value.
- Historical trend viewing for vibration, temperature, alerts, and maintenance behavior.
- Basic dashboard or report interface for users.

The system will not initially support full-scale industrial deployment, automatic machine shutdown, advanced enterprise integration, or complete replacement of professional maintenance teams.

### 1.3 Definitions, Acronyms, and Abbreviations

| Term | Meaning |
| --- | --- |
| IoT | Internet of Things, a network of connected devices that collect and exchange data |
| SRS | Software Requirements Specification |
| Predictive Maintenance | Maintenance strategy that predicts failures before they happen |
| Vibration Sensor | Sensor used to measure movement, oscillation, or vibration of a machine |
| Temperature Sensor | Sensor used to measure machine temperature, overheating, friction, or load stress indicators |
| ML | Machine Learning |
| Dashboard | User interface used to display machine health and alerts |
| Fault Classification | Process of identifying the type of machine fault |
| Severity Score | Numeric or categorical value representing the seriousness of a fault |
| Root Cause | Probable reason behind an abnormal condition, such as bearing wear, imbalance, overheating, or misalignment |
| Edge Layer | Local device or gateway layer that collects, tags, buffers, and optionally preprocesses sensor data |
| Cloud Layer | Server or cloud layer that stores data, performs analysis, generates alerts, and serves dashboard views |
| KPI | Key Performance Indicator used to evaluate machine health, maintenance response, prediction quality, system performance, or operational value |

### 1.4 Intended Users

The system is intended for the following users:

- Maintenance operators who need to monitor machine health.
- Supervisors who need alerts and summaries of machine condition.
- Students and project evaluators who need to understand the system flow and technical implementation.
- Developers who will implement, test, and improve the prototype.

### 1.5 References

- Project context document: `PROJECT_CONTEXT.md`
- General predictive maintenance and vibration monitoring concepts
- Standard SRS structure used in academic software engineering projects

---

## 2. Overall Description

### 2.1 Product Perspective

The Smart Predictive Maintenance Decision-Support System is an IoT and machine learning based monitoring solution. It acts as a bridge between machine hardware and maintenance decision-making.

Traditional systems often depend on scheduled maintenance, manual inspection, or repairs after a failure occurs. The proposed system improves this approach by continuously observing vibration and temperature signals, detecting early warning signs, and converting prediction results into maintenance decisions.

### 2.2 Product Functions

At a high level, the system will perform the following functions:

1. Collect vibration and temperature data from sensors attached to a machine or from stored datasets.
2. Tag each record with timestamp, machine identifier, and sensor identifier.
3. Buffer data at the edge layer if connectivity fails.
4. Transfer collected data to a local server, cloud server, or processing unit.
5. Preprocess raw vibration and temperature readings.
6. Extract useful vibration and temperature features.
7. Analyze data using a machine learning model or rule-based logic.
8. Classify the machine state as healthy, degrading, faulty, or critical.
9. Identify a probable root cause for abnormal conditions.
10. Generate alerts when abnormal patterns are found.
11. Track last maintenance date, days since maintenance, and maintenance actions.
12. Display machine status, root cause, alerts, KPIs, maintenance history, and historical trends through a dashboard or report.

### 2.3 User Classes and Characteristics

| User Class | Description | Expected Technical Knowledge |
| --- | --- | --- |
| Maintenance Operator | Checks alerts and machine condition | Basic technical knowledge |
| Supervisor | Reviews system output and maintenance priority | Moderate operational knowledge |
| Student Developer | Builds and tests the prototype | Programming, IoT, and ML knowledge |
| Professor or Evaluator | Evaluates project concept, design, and implementation | Academic and technical understanding |

### 2.4 Operating Environment

The system may operate in one of the following environments:

- A local prototype setup using a vibration sensor, microcontroller, and laptop.
- A cloud-connected setup where data is sent to a server or cloud platform.
- A simulated setup using stored vibration and temperature datasets for testing and demonstration.

Possible hardware and software components include:

- Vibration sensor or accelerometer.
- Temperature sensor.
- Microcontroller such as Arduino, ESP32, or Raspberry Pi.
- Local computer or cloud server.
- Python-based data processing and ML environment.
- Web dashboard or simple user interface.

### 2.5 Design and Implementation Constraints

- Sensor quality, calibration, and placement may affect data accuracy.
- Machine learning model performance depends on the quality and quantity of data.
- Real-time processing may be limited by hardware capacity.
- The academic prototype may use simulated or limited machine data.
- Internet connectivity may be required if cloud storage or remote alerts are used.
- Root cause output should be treated as a probable cause, not a final diagnosis.
- KPI values such as root cause accuracy or operational improvement may require labeled data or longer observation periods.
- The system should be developed within the time and resource limits of a college project.

### 2.6 Assumptions and Dependencies

The system assumes that:

- Vibration and temperature behavior may change when a machine develops a fault.
- A sensor setup or dataset will be available for collecting or simulating vibration and temperature data.
- The project team has access to basic computing resources.
- The user will review alerts and decide the final maintenance action.
- The system is meant to assist maintenance decisions, not replace human judgment.

Dependencies may include:

- Vibration and temperature sensor hardware availability.
- Microcontroller or data acquisition module.
- Python libraries for data processing and machine learning.
- Local or cloud database, if historical storage is implemented.
- Dashboard framework, if a graphical interface is developed.

---

## 3. System Features and Functional Requirements

### 3.1 Data Collection

**Description:**  
The system shall collect vibration and temperature data from a machine through sensors or from stored datasets.

| Requirement ID | Requirement |
| --- | --- |
| FR-01 | The system shall collect vibration readings from a sensor or input dataset. |
| FR-02 | The system shall collect temperature readings from a sensor or input dataset when temperature monitoring is included in the prototype. |
| FR-03 | The system shall support continuous or periodic data collection. |
| FR-04 | The system shall record each reading with timestamp, machine ID, and sensor ID. |
| FR-05 | The system shall identify the source machine or test setup for each data record, if multiple machines are supported. |

### 3.2 Data Transmission

**Description:**  
The system shall transfer collected sensor data to a processing unit for analysis.

| Requirement ID | Requirement |
| --- | --- |
| FR-06 | The system shall send vibration and temperature data from the sensor module to a local computer, server, or cloud platform. |
| FR-07 | The system shall handle temporary communication failure by storing data locally at the edge layer when possible. |
| FR-08 | The system shall support wired or wireless data transfer depending on the prototype setup. |
| FR-09 | The system shall synchronize locally buffered records with the server or cloud layer after connectivity is restored. |

### 3.3 Data Preprocessing

**Description:**  
Raw vibration signals may contain noise or inconsistent values. The system shall prepare the data before analysis.

| Requirement ID | Requirement |
| --- | --- |
| FR-10 | The system shall clean missing, invalid, or noisy readings where possible. |
| FR-11 | The system shall normalize or scale vibration and temperature data before model analysis when required. |
| FR-12 | The system shall divide sensor data into suitable time windows or samples for analysis. |
| FR-13 | The system shall calculate missing or invalid reading percentages for data-quality monitoring. |

### 3.4 Feature Extraction

**Description:**  
The system shall extract useful information from vibration and temperature signals to support fault detection.

| Requirement ID | Requirement |
| --- | --- |
| FR-14 | The system shall calculate basic vibration features such as mean, standard deviation, peak value, and RMS value. |
| FR-15 | The system should support frequency-domain vibration features if FFT or similar analysis is implemented. |
| FR-16 | The system shall calculate temperature features such as current value, rolling average, rate of increase, and threshold severity when temperature data is available. |
| FR-17 | The extracted features shall be stored or passed to the machine learning model or rule engine for classification. |

### 3.5 Machine Health Analysis

**Description:**  
The system shall analyze multi-sensor patterns and determine the machine condition.

| Requirement ID | Requirement |
| --- | --- |
| FR-18 | The system shall classify machine health into states such as healthy, degrading, faulty, and critical. |
| FR-19 | The system should classify fault type if labeled data is available. |
| FR-20 | The system should calculate vibration severity, temperature severity, and a combined machine risk score. |
| FR-21 | The system shall compare current vibration and temperature behavior with learned or predefined normal behavior. |
| FR-22 | The system should provide a confidence score for each health prediction. |

### 3.6 Root Cause Identification

**Description:**  
The system shall identify a probable root cause for an abnormal machine condition.

| Requirement ID | Requirement |
| --- | --- |
| FR-23 | The system shall provide a probable root cause when a machine is classified as degrading, faulty, or critical. |
| FR-24 | The system should support root cause categories such as bearing wear, misalignment, imbalance, overheating, excessive load, loose mounting, mechanical looseness, and sensor or data-quality issue. |
| FR-25 | The root cause output shall be shown as a probable cause and not as a final diagnosis. |
| FR-26 | The system should provide a confidence score for probable root cause when supported by the model or rule logic. |

### 3.7 Alert Generation

**Description:**  
The system shall notify the user when abnormal behavior is detected.

| Requirement ID | Requirement |
| --- | --- |
| FR-27 | The system shall generate an alert when machine condition is classified as degrading, faulty, or critical. |
| FR-28 | The alert shall include machine status, time, severity level, probable root cause, and possible issue description. |
| FR-29 | The system should avoid repeated duplicate alerts for the same unresolved issue. |
| FR-30 | The system should support alert delivery through dashboard notification, email, SMS, or mobile notification depending on implementation scope. |

### 3.8 Dashboard and Reporting

**Description:**  
The system shall provide a user interface or report for viewing machine condition, maintenance status, and KPIs.

| Requirement ID | Requirement |
| --- | --- |
| FR-31 | The system shall display current machine health status. |
| FR-32 | The system shall display vibration severity, temperature severity, and combined machine risk score. |
| FR-33 | The system shall display recent alerts and abnormal events. |
| FR-34 | The system should show historical vibration and temperature trends using graphs or tables. |
| FR-35 | The system should display probable root cause and confidence where available. |
| FR-36 | The system should provide maintenance recommendations based on severity level, root cause, and maintenance history. |
| FR-37 | The system shall display last maintenance date and days since last maintenance for each monitored machine. |
| FR-38 | The system should display project KPIs for machine health, maintenance response, prediction quality, system performance, and operational value. |

### 3.9 Data Storage

**Description:**  
The system shall store sensor data, analysis results, alerts, and maintenance history for later review.

| Requirement ID | Requirement |
| --- | --- |
| FR-39 | The system shall store processed vibration and temperature records and machine status results. |
| FR-40 | The system should store historical alert logs. |
| FR-41 | The system should store maintenance history, including last maintenance date and completed maintenance actions. |
| FR-42 | The system should allow retrieval of past machine health data for trend analysis. |
| FR-43 | The system should store KPI values or calculated KPI summaries when implemented. |

---

## 4. External Interface Requirements

### 4.1 User Interface

The user interface should be simple and easy to understand. It may be implemented as a web dashboard, desktop interface, or structured report.

The interface should include:

- Machine health status.
- Vibration trend graph.
- Temperature trend graph.
- Alert list.
- Severity level.
- Probable root cause.
- Combined risk score.
- Last maintenance date and days since last maintenance.
- KPI summary.
- Maintenance recommendation.
- Last updated timestamp.

### 4.2 Hardware Interface

The system may interface with:

- Vibration sensor or accelerometer.
- Temperature sensor.
- Microcontroller or data acquisition board.
- Machine under monitoring.
- Local edge gateway, computer, or cloud-connected gateway.

### 4.3 Software Interface

The system may use:

- Python for preprocessing and machine learning.
- Database or file storage for historical data.
- Web framework for dashboard development.
- Rule engine or ML model for probable root cause identification.
- KPI calculation module for dashboard metrics.
- Communication protocol such as serial, Wi-Fi, MQTT, HTTP, or Bluetooth depending on the selected hardware.

### 4.4 Communication Interface

The system should support reliable data transfer between the sensor module, edge gateway, and cloud or server layer. If cloud connectivity is used, the system should use standard communication protocols such as HTTP or MQTT. The edge layer should temporarily buffer records during network failure and synchronize them after connectivity is restored.

---

## 5. Non-Functional Requirements

### 5.1 Performance Requirements

| Requirement ID | Requirement |
| --- | --- |
| NFR-01 | The system should process vibration and temperature samples within a reasonable time for near real-time monitoring. |
| NFR-02 | The dashboard should update machine status without noticeable delay in the prototype environment. |
| NFR-03 | The system should handle continuous data collection for the selected demonstration duration. |
| NFR-04 | The system should track dashboard update latency and data transmission success rate during testing when possible. |

### 5.2 Reliability Requirements

| Requirement ID | Requirement |
| --- | --- |
| NFR-05 | The system should continue operating during minor sensor noise or temporary invalid readings. |
| NFR-06 | The system should avoid generating repeated duplicate alerts for the same unresolved issue. |
| NFR-07 | The system should store important analysis results to prevent data loss during demonstration. |
| NFR-08 | The edge layer should preserve locally buffered data during temporary network failure where hardware support allows it. |

### 5.3 Accuracy Requirements

| Requirement ID | Requirement |
| --- | --- |
| NFR-09 | The system should correctly identify normal and abnormal vibration and temperature patterns with acceptable accuracy for an academic prototype. |
| NFR-10 | The system should be tested using known normal and abnormal samples where possible. |
| NFR-11 | The system should clearly show confidence, severity, or limitation when prediction certainty is low. |
| NFR-12 | Root cause accuracy should be evaluated if labeled root cause data is available. |

### 5.4 Usability Requirements

| Requirement ID | Requirement |
| --- | --- |
| NFR-13 | The dashboard or report should be understandable to a user without advanced machine learning knowledge. |
| NFR-14 | Alerts should use clear language, severity labels, probable cause, and recommended next action. |
| NFR-15 | The system should present important machine health, maintenance, and KPI information in a simple and organized format. |

### 5.5 Maintainability Requirements

| Requirement ID | Requirement |
| --- | --- |
| NFR-16 | The software should be modular, separating data collection, preprocessing, model analysis, root cause logic, alerting, maintenance history, KPI calculation, and dashboard functions. |
| NFR-17 | The system should allow future addition of new sensors, fault classes, root cause categories, or machine types. |
| NFR-18 | The code and documentation should be clear enough for future student teams to understand and improve. |

### 5.6 Security Requirements

| Requirement ID | Requirement |
| --- | --- |
| NFR-19 | If the system uses cloud communication, data transfer should be protected using authentication or secure APIs where possible. |
| NFR-20 | User access to dashboard data should be limited if multiple users are supported. |
| NFR-21 | The system should not expose sensitive machine or user data unnecessarily. |

---

## 6. Proposed System Architecture

### 6.1 High-Level Architecture

The proposed system should follow an edge-cloud split architecture:

1. **Machine Layer:** Physical machine such as motor, pump, fan, or bearing setup.
2. **Sensor Layer:** Vibration and temperature sensors attached to the machine.
3. **Edge Layer:** Microcontroller or gateway that collects data, tags machine ID and timestamp, performs optional lightweight preprocessing, and temporarily buffers records if connectivity fails.
4. **Cloud or Server Layer:** Software layer that stores data, preprocesses records, extracts features, performs ML or rule-based analysis, predicts root cause, calculates KPIs, and generates alerts.
5. **Storage Layer:** Database or file storage for sensor history, predictions, alerts, root cause output, maintenance records, and KPI summaries.
6. **Application Layer:** Dashboard or report interface for users.

### 6.2 Data Flow

1. Sensors capture vibration and temperature readings from the machine.
2. Edge gateway reads sensor values and tags each record with timestamp, machine ID, and sensor ID.
3. Edge gateway temporarily buffers records if connectivity is unavailable.
4. Data is transmitted or synchronized to a local server or cloud system.
5. The processing module cleans and prepares the data.
6. Vibration and temperature features are extracted from the readings.
7. The ML model or rule engine predicts machine health, severity, and probable root cause.
8. The result is stored with alert and maintenance context.
9. If the result is abnormal, an alert is generated.
10. Dashboard shows current status, probable root cause, maintenance history, KPIs, and recommended next action.

### 6.3 Suggested Modules

| Module | Responsibility |
| --- | --- |
| Sensor Module | Collect vibration and temperature readings from the machine |
| Edge Gateway Module | Tag readings with timestamp and machine ID, buffer records during temporary connectivity loss, and forward data |
| Communication Module | Transfer readings to local server, cloud, or processing system |
| Preprocessing Module | Clean, normalize, and prepare vibration and temperature data |
| Feature Extraction Module | Calculate useful vibration and temperature features |
| Prediction Module | Detect machine condition using ML or rules |
| Root Cause Module | Estimate probable cause for abnormal conditions |
| Alert Module | Generate and manage alerts with severity, root cause, and recommendation |
| Maintenance History Module | Store last maintenance date, days since maintenance, and completed actions |
| KPI Module | Calculate machine health, maintenance, prediction, system, and operational KPIs |
| Dashboard Module | Display machine status, trends, root cause, alerts, maintenance history, and KPIs |
| Storage Module | Store sensor history, predictions, root cause results, alerts, maintenance records, and KPI summaries |

---

## 7. Data Requirements

### 7.1 Input Data

The main input data will be vibration and temperature readings. Depending on the sensor setup, the data may include:

- Acceleration on one or more axes.
- Temperature value.
- Timestamp.
- Machine identifier.
- Sensor identifier.
- Sampling rate.
- Optional operating condition data such as load or speed.
- Last maintenance date or maintenance action record.

### 7.2 Output Data

The system output may include:

- Machine health status: healthy, degrading, faulty, or critical.
- Probable root cause, if implemented.
- Severity score.
- Vibration severity level.
- Temperature severity level.
- Combined machine risk score.
- Alert message.
- Maintenance recommendation.
- Last maintenance date and days since last maintenance.
- Historical vibration and temperature trends.
- Model confidence or prediction score.
- KPI summaries.

### 7.3 Data Storage Format

Data may be stored in:

- CSV files for simple academic demonstration.
- SQLite or another lightweight database.
- Cloud database if remote dashboard functionality is implemented.

### 7.4 KPI Data

The system should track clear KPIs so the project can be evaluated beyond only model output.

| KPI Category | Example KPIs |
| --- | --- |
| Machine Health KPIs | Current health status, vibration severity, temperature severity, combined machine risk score, abnormal event count, repeated alerts for the same machine |
| Maintenance KPIs | Last maintenance date, days since last maintenance, completed maintenance actions, unresolved alerts, average alert acknowledgement time, average alert resolution time, machines due for inspection |
| Prediction and Model KPIs | Prediction accuracy, false alarm count, missed fault count, health prediction confidence, root cause confidence, root cause accuracy if labeled data is available |
| System Performance KPIs | Sensor data collection rate, data transmission success rate, edge buffer usage during network failure, cloud synchronization success rate, dashboard update latency, missing or invalid reading percentage |
| Operational Value KPIs | Reduction in unexpected breakdowns, reduction in emergency maintenance cases, improvement in planned maintenance actions, faults detected before failure, maintenance priority score for each machine |

---

## 8. Use Cases

### 8.1 Use Case 1: Monitor Machine Health

| Field | Description |
| --- | --- |
| Actor | Maintenance operator |
| Goal | View current machine condition |
| Precondition | Sensor setup or dataset is available |
| Main Flow | System collects vibration and temperature data, analyzes it, and displays health status |
| Output | Machine shown as healthy, degrading, faulty, or critical |

### 8.2 Use Case 2: Identify Probable Root Cause

| Field | Description |
| --- | --- |
| Actor | System and maintenance operator |
| Goal | Understand the likely reason for an abnormal machine condition |
| Precondition | Vibration and/or temperature readings show abnormal behavior |
| Main Flow | System analyzes features and classifies a probable cause such as bearing wear, imbalance, misalignment, overheating, excessive load, looseness, or data-quality issue |
| Output | Probable root cause with confidence or explanation |

### 8.3 Use Case 3: Detect Abnormal Condition

| Field | Description |
| --- | --- |
| Actor | System |
| Goal | Detect unusual vibration or temperature pattern |
| Precondition | Data has been collected and processed |
| Main Flow | Model compares current pattern with normal behavior |
| Output | Abnormal condition detected and recorded |

### 8.4 Use Case 4: Generate Maintenance Alert

| Field | Description |
| --- | --- |
| Actor | System and maintenance operator |
| Goal | Notify user about possible machine fault |
| Precondition | Abnormal condition is detected |
| Main Flow | System creates an alert with severity and recommendation |
| Output | User receives warning and can inspect the machine |

### 8.5 Use Case 5: View Historical Trends

| Field | Description |
| --- | --- |
| Actor | Supervisor or operator |
| Goal | Understand vibration, temperature, and alert changes over time |
| Precondition | Historical data is stored |
| Main Flow | User opens dashboard and reviews trend graph or report |
| Output | User identifies increasing vibration, overheating, or repeated abnormal events |

### 8.6 Use Case 6: Review Maintenance History

| Field | Description |
| --- | --- |
| Actor | Maintenance operator or supervisor |
| Goal | Prioritize machines for inspection based on condition and maintenance age |
| Precondition | Maintenance records and machine status are available |
| Main Flow | User views last maintenance date, days since maintenance, health status, recent alerts, and recommended next action |
| Output | Machine inspection or maintenance priority is identified |

### 8.7 Use Case 7: Track Project KPIs

| Field | Description |
| --- | --- |
| Actor | Supervisor, student developer, or evaluator |
| Goal | Evaluate machine health, maintenance response, prediction quality, system performance, and operational value |
| Precondition | Data has been collected over a testing or demonstration period |
| Main Flow | Dashboard calculates and displays KPI summaries |
| Output | User can judge whether the prototype is useful beyond model classification |

---

## 9. Acceptance Criteria

The project will be considered successful if:

- The system can collect or load vibration and temperature data.
- The system can preprocess vibration and temperature readings.
- The system can classify or detect machine health condition.
- The system can identify abnormal vibration or temperature behavior.
- The system can identify a probable root cause for abnormal conditions.
- The system can generate an alert for possible fault conditions.
- The system can show current machine status, severity, root cause, maintenance history, and recommended next action through a dashboard, report, or output screen.
- The system can track last maintenance date and days since last maintenance for each monitored machine.
- The system can define and display KPIs for machine health, maintenance response, prediction quality, system performance, and operational value.
- The system can explain the edge-cloud split, including edge data collection, tagging, buffering, cloud/server analysis, storage, alerts, and dashboard visualization.
- The system can explain how predictive maintenance is better than reactive maintenance.
- The final demonstration clearly connects sensor data, analysis, prediction, and maintenance decision-making.

---

## 10. Risks and Mitigation

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Sensor data is noisy | Model predictions may be inaccurate | Use filtering, preprocessing, and repeated readings |
| Temperature sensor is missing or inaccurate | Multi-sensor reliability may be reduced | Support dataset-based temperature input or clearly mark temperature data as optional for prototype testing |
| Limited fault data | ML model may not generalize well | Use simulated data, public datasets, or rule-based detection for prototype |
| Limited root cause labels | Root cause accuracy may be difficult to validate | Show root cause as probable and evaluate accuracy only if labeled data is available |
| Hardware setup is unavailable | Physical demonstration may be difficult | Use stored vibration and temperature datasets for software demonstration |
| Alerts are too frequent | User may ignore warnings | Add severity levels and avoid duplicate alerts |
| Model accuracy is low | System may produce wrong predictions | Test with multiple samples and clearly document limitations |
| Network connection fails | Cloud dashboard may miss new data temporarily | Use edge buffering and synchronize data when connection returns |
| KPI improvement is hard to prove in a short project | Operational impact may be theoretical | Track prototype KPIs and explain long-term operational KPIs as future evaluation measures |

---

## 11. Future Enhancements

Future versions of the system may include:

- Advanced fault classification and root cause models for bearing wear, imbalance, misalignment, overheating, excessive load, and looseness.
- Integration with cloud IoT platforms and scalable edge gateway management.
- Mobile app notifications.
- Real-time streaming dashboard.
- Predictive remaining useful life estimation.
- Multi-machine monitoring.
- Automated maintenance scheduling.
- Integration with industrial control or ERP systems.
- More sensors such as current, acoustic, pressure, or oil-quality monitoring.
- Closed-loop maintenance workflow with acknowledgement, assignment, and resolution tracking.

---

## 12. Conclusion

The Smart Predictive Maintenance Decision-Support System using vibration and temperature data provides a practical and valuable approach to machine health monitoring. By using IoT sensors, edge-cloud data flow, machine learning or rule-based analysis, root cause identification, maintenance history, and KPI tracking, the system can detect early signs of failure and help users take maintenance action before serious breakdowns occur.

For an academic project, this system demonstrates important concepts from IoT, signal processing, machine learning, data visualization, and industrial automation. It also addresses a real-world industrial problem by moving from reactive maintenance to predictive maintenance.
