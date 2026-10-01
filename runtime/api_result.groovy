// Prevent a View Results Tree listener from retaining the outgoing bearer header.
prev.setRequestHeaders('')
prev.setSamplerData('')
if (prev.getResponseCode() == '401' || prev.getResponseCode() == '403') {
    vars.remove('access_token')
    vars.remove('__renew_at_nanos')
}
// No automatic replay: a POST might already have changed server state.
