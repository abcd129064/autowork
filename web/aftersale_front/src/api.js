import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 15000 })

export function fetchRecords(params) {
  return http.get('/records', { params }).then(r => r.data)
}

export function fetchCycleOptions() {
  return http.get('/cycle-options').then(r => r.data)
}

export function fetchHealth() {
  return http.get('/health').then(r => r.data)
}

export function fetchCharts(params) {
  return http.get('/stats/charts', { params }).then(r => r.data)
}

export function fetchQuery(payload) {
  return http.post('/stats/query', payload).then(r => r.data)
}
