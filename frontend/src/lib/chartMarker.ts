import type { TrendPoint } from '../api/types'

export interface ChartMarker {
  timestamp: string
  label: string
  index?: number
}

// Index of the point the marker sits on, or null when it isn't in the data.
// The x axis is keyed by point index, not by the minute label: sub-minute
// data (2 s MQTT telemetry) repeats labels, and a category axis with
// duplicates switches to an index domain where a label x draws nothing.
export function markerIndex(points: TrendPoint[], marker: ChartMarker | undefined): number | null {
  if (!marker) return null
  if (marker.index !== undefined) {
    return marker.index >= 0 && marker.index < points.length ? marker.index : null
  }
  for (let i = points.length - 1; i >= 0; i--) {
    if (points[i].timestamp === marker.timestamp) return i
  }
  return null
}
