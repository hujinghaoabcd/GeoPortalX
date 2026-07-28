import { createPinia } from 'pinia';
import { createApp } from 'vue';
import 'maplibre-gl/dist/maplibre-gl.css';

import App from './App.vue';
import RasterApp from './RasterApp.vue';
import './style.css';

const query = new URLSearchParams(window.location.search);
const Root = query.has('rasterDataset') ? RasterApp : App;

createApp(Root).use(createPinia()).mount('#app');
