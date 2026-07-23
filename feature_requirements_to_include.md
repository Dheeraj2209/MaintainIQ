# Feature Requirements to Include

## 1. Multi-Sensor Monitoring

The system should support multi-sensor condition monitoring, mainly using:

- Vibration sensor data
- Temperature sensor data

The vibration sensor will capture mechanical behavior such as imbalance, looseness, bearing wear, or abnormal vibration patterns. The temperature sensor will help identify overheating, friction, load stress, or cooling-related issues.

Combining vibration and temperature data should make the health prediction more reliable than using vibration alone.

## 2. Root Cause Identification

The system should not only classify the machine as healthy or faulty. It should also provide a possible root cause for the detected abnormal condition.

Possible root cause categories may include:

- Bearing wear
- Misalignment
- Imbalance
- Overheating
- Excessive load
- Loose mounting or mechanical looseness
- Sensor or data-quality issue

The root cause result can be shown as a probable cause, not as a final diagnosis. This keeps the system realistic while still making the output more useful for maintenance decisions.

## 3. Maintenance History Tracking

The dashboard should track the last maintenance day for each monitored machine.

The dashboard should show:

- Last maintenance date
- Number of days since last maintenance
- Latest machine health status
- Recent alerts or abnormal events
- Recommended next action

This helps connect prediction results with real maintenance planning. A machine with abnormal readings and a long time since last maintenance can be prioritized more urgently.

## 4. Edge-Cloud Split Architecture

The system should follow an edge-cloud split architecture.

The edge layer should handle:

- Sensor data collection
- Basic timestamping and machine ID tagging
- Temporary local buffering if connectivity fails
- Optional lightweight preprocessing or feature calculation

The cloud or server layer should handle:

- Data storage
- ML or rule-based health analysis
- Root cause prediction
- Alert generation
- Dashboard and historical trend visualization

This architecture makes the system more scalable and realistic. It also allows the system to keep collecting data locally even when the cloud or network connection is temporarily unavailable.

## 5. KPIs to Track

The project should define clear KPIs so the system can be evaluated beyond just whether the ML model works.

### Machine Health KPIs

- Current machine health status: healthy, degrading, faulty, or critical
- Vibration severity level
- Temperature severity level
- Combined machine risk score
- Number of abnormal events detected
- Number of repeated alerts for the same machine

### Maintenance KPIs

- Last maintenance date
- Days since last maintenance
- Number of maintenance actions completed
- Number of unresolved alerts
- Average time taken to acknowledge an alert
- Average time taken to resolve an alert
- Machines due for inspection

### Prediction and Model KPIs

- Prediction accuracy
- False alarm count
- Missed fault count
- Confidence score for health prediction
- Confidence score for probable root cause
- Root cause prediction accuracy, if labeled data is available

### System Performance KPIs

- Sensor data collection rate
- Data transmission success rate
- Edge buffer usage during network failure
- Cloud synchronization success rate
- Dashboard update latency
- Percentage of missing or invalid sensor readings

### Operational Value KPIs

- Reduction in unexpected breakdowns
- Reduction in emergency maintenance cases
- Improvement in planned maintenance actions
- Number of faults detected before failure
- Maintenance priority score for each machine

## Updated Project Scope

The project should be framed as a smart predictive maintenance decision-support system, not only as a vibration-based ML classifier.

Updated scope:

> A multi-sensor predictive maintenance system that uses vibration and temperature data to monitor machine condition, classify health status, identify probable root causes, track maintenance history, and support an edge-cloud architecture for scalable monitoring.
