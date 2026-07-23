# PlantUML Diagrams

This folder contains PlantUML source files and rendered diagram outputs for the smart predictive maintenance design. Each source file has a matching `.svg` and `.png` in `rendered/`.

## Files

| Source | Rendered output | Purpose |
|---|---|---|
| `factory_scenario_description.puml` | `rendered/factory_scenario_description.svg` | Factory floor scenario with three example machines |
| `system_architecture_diagram.puml` | `rendered/system_architecture_diagram.svg` | Layered edge-to-cloud system architecture |
| `multimachine_uml_class.puml` | `rendered/multimachine_uml_class.svg` | Multi-machine UML/class model |
| `sensor_module_block_diagram.puml` | `rendered/sensor_module_block_diagram.svg` | Sensor module and gateway/cloud connectivity |
| `sensor_selection_table.puml` | `rendered/sensor_selection_table.svg` | Temperature and vibration sensor selection comparison |
| `esp32_pin_interface_table.puml` | `rendered/esp32_pin_interface_table.svg` | ESP32 sensor/pin/interface mapping |
| `communication_protocol_comparison.puml` | `rendered/communication_protocol_comparison.svg` | Protocol comparison for MQTT, HTTP, BLE, serial, RS485, and others |
| `data_flow_diagram.puml` | `rendered/data_flow_diagram.svg` | Data flow from machine sensors to dashboard and alerts |
| `non_functional_study.puml` | `rendered/non_functional_study.svg` | Interoperability, scalability, cost, and security study |
| `training_inference_upstream_downstream.puml` | `rendered/training_inference_upstream_downstream.svg` | Offline training, deployed inference, upstream telemetry, downstream alerts, and serial debugging |
| `training_inference_upstream_downstream_simple.svg` | `rendered/training_inference_upstream_downstream_simple.png` | Simplified slide-friendly view of cloud-above, machine-below upstream/downstream flow |

## Recommended Architecture Captured

Each machine has its own ESP32 sensor node. Temperature and vibration sensors are wired locally to that ESP32, and each ESP32 publishes machine telemetry over Wi-Fi using MQTT. Alerts can be sent back from the gateway/cloud to the ESP32 through MQTT alert topics. RS485/Modbus is documented as a future industrial scalability alternative.

The training/inference diagram clarifies that model training happens offline on the local machine, while the deployed backend performs repeated runtime inference on incoming MQTT telemetry batches or windows.
