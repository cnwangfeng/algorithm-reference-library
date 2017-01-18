# Tim Cornwell <realtimcornwell@gmail.com>
#
"""
Functions that aid fourier transform processing. These are built on top of the core
functions in arl.fourier_transforms.
"""

from arl.fourier_transforms.convolutional_gridding import frac_coord
from arl.fourier_transforms.fft_support import *
from arl.fourier_transforms.ftprocessor import *

log = logging.getLogger(__name__)


def compress_visibility(vis, im, **kwargs):
    """ Compress the visibility data using a grid

    Compress by gridding the visibilities onto a fine grid and then extracting the visibilities, weights and uvw
    from the grid. The maximum number of rows in the output visibility is the same number as the number of pixels
    in each polarisation-frequency plane i.e. nx, ny

    :param vis:
    :param im:
    :param compression: Only "uvgrid" is currently supported
    :returns: Derived Visibility
    """
    nchan, npol, ny, nx, shape, gcf, kernel_type, kernelname, kernel, padding, oversampling, support, cellsize, \
    fov, uvscale = get_ftprocessor_params(vis, im, **kwargs)
    
    assert nx == im.data.shape[3], "Discrepancy between npixel and size of model image"
    assert ny == im.data.shape[2], "Discrepancy between npixel and size of model image"
    
    compression = get_parameter(kwargs, "compression", "uvgrid")
    if compression == 'uvgrid':
        cvis, cuvw, cvisweights = compress_grid_vis(im.data.shape, vis.data['vis'], vis.data['uvw'], uvscale,
                                                    vis.data['weight'])
        nrows = cvis.shape[0]
        ca1 = numpy.zeros([nrows])
        ca2 = numpy.zeros([nrows])
        cimwt = numpy.ones(cvis.shape)
        ctime = numpy.ones(nrows) * numpy.average(vis.data['time'])
        compressed_vis = Visibility(uvw=cuvw, time=ctime, frequency=vis.frequency, phasecentre=vis.phasecentre,
                                    antenna1=ca1, antenna2=ca2, vis=cvis, weight=cvisweights,
                                    imaging_weight=cimwt, configuration=vis.configuration)
        log.info('compress_visibility: Compressed %d visibility rows into %d rows on a regular grid' %
                 (vis.nvis, compressed_vis.nvis))
    
    else:
        log.error("Unknown visibility compression algorithm %s" % compression)
    return compressed_vis


def decompress_visibility(vis, template_vis, im, **kwargs):
    """ Decompress the visibilities from a gridded set to the original values (opposite of compress_visibility)

    :param vis: (Compressed visibility
    :param template_vis: Template visibility to be filled in
    :param im: Image specifying coordinates of image (must be consistent with vis)
    :param compression: Only "uvgrid" is currently supported
    :returns: Template visibility with vis and weight columns overwritten
    """
    nchan, npol, ny, nx, shape, gcf, kernel_type, kernelname, kernel, padding, oversampling, support, cellsize, \
    fov, uvscale = get_ftprocessor_params(vis, im, **kwargs)
    
    assert nx == im.data.shape[3], "Discrepancy between npixel and size of model image"
    assert ny == im.data.shape[2], "Discrepancy between npixel and size of model image"
    
    compression = get_parameter(kwargs, "compression", "uvgrid")
    if compression == 'uvgrid':
        log.info('compress_visibility: Decompressing %d visibility rows on a regular grid into %d rows' %
                 (vis.nvis, template_vis.nvis))
        template_vis.data['vis'], template_vis.data['weight'] = \
            decompress_grid_vis(im.data.shape,
                                template_vis.data['vis'],
                                template_vis.data['weight'],
                                template_vis.data['uvw'],
                                vis.data['vis'],
                                vis.data['weight'],
                                vis.data['uvw'],
                                uvscale)
    
    else:
        log.error("Unknown visibility compression algorithm %s" % compression)
    return template_vis


def compress_grid_vis(shape, vis, uvw, uvscale, visweights):
    """Compress data by gridding with box and then converting the grid to visibilities, uvw, and weights
    
    :param shape: Shape of grid to be used => shape of output visibilities
    :param uv: Input UVW positions
    :param uvscale: Scaling for each axis (u,v) for each channel
    :param vis: Visibility values
    :param visweights: Visibility weights
    :returns: vis, uvw, visweights
    """
    nchan, npol, ny, nx = shape
    
    rshape = ny, nx, nchan, npol
    visgrid = numpy.zeros(rshape, dtype='complex')
    wtsgrid = numpy.zeros(rshape)
    # Add all visibility points to a float grid
    for chan in range(nchan):
        y, _ = frac_coord(ny, 1, uvscale[1, chan] * uvw[..., 1])
        x, _ = frac_coord(nx, 1, uvscale[0, chan] * uvw[..., 0])
        coords = x, y
        for pol in range(npol):
            vs = vis[..., chan, pol]
            wts = visweights[..., chan, pol]
            for v, wt, x, y, in zip(vs, wts, *coords):
                # Note that this ordering is different from that in other gridding
                # functions because we want to do a reshape to get the result into
                # the correct order
                visgrid[y, x, chan, pol] += v
                wtsgrid[y, x, chan, pol] += wt

    visgrid[wtsgrid>0.0] /= wtsgrid[wtsgrid>0.0]
    visgrid[wtsgrid<=0.0] = 0.0
    
    # These just need a reshape
    nvis = nx * ny
    cvis = visgrid.reshape(nvis, nchan, npol)
    cvisweights = wtsgrid.reshape(nvis, nchan, npol)
    
    # Need to convert back to metres at the first frequency
    cu = (numpy.arange(nx) - nx // 2) / (nx * uvscale[0, 0])
    cv = (numpy.arange(ny) - ny // 2) / (ny * uvscale[1, 0])
    cuvw = numpy.zeros([nx * ny, 3])
    for v in range(len(cv)):
        for u in range(len(cu)):
            cuvw[u+len(cu)*v, 0] = cu[u]
            cuvw[u+len(cu)*v, 1] = cv[v]
            
    # Construct row mask
    rowmask = numpy.where(cvisweights > 0)[0]
    return cvis[rowmask,...], cuvw[rowmask,...], cvisweights[rowmask,...]


def decompress_grid_vis(shape, tvis, twts, tuvw, vis, visweights, uvw, uvscale):
    """Decompress data using one of a number of algorithms

    :param shape:
    :param tvis: Template visibility
    :param twts: Template weights
    :param tuvw: Template UVW positions
    :param vis: Visibility values
    :param visweights: Visibility weights
    :param uvscale: Scaling for each axis (u,v) for each channel
    """
    nchan, npol, ny, nx = shape
    
    rshape = ny, nx, nchan, npol
    visgrid = numpy.zeros(rshape, dtype='complex')
    wtsgrid = numpy.zeros(rshape)
    # First rebuild the full grid. We could have kept it cached.
    for chan in range(nchan):
        yy, _ = frac_coord(ny, 1, uvscale[1, chan] * uvw[..., 1])
        xx, _ = frac_coord(nx, 1, uvscale[0, chan] * uvw[..., 0])
        coords = xx, yy
        for pol in range(npol):
            vs = vis[..., chan, pol]
            wts = visweights[..., chan, pol]
            for v, wt, x, y, in zip(vs, wts, *coords):
                # Note that this ordering is different from that in other gridding
                # functions because we want to do a reshape to get the result into
                # the correct order
                # Note also that this is a one-to-one mapping
                visgrid[y, x, chan, pol] = v
                wtsgrid[y, x, chan, pol] = wt

    for chan in range(nchan):
        y, _ = frac_coord(ny, 1, uvscale[1, chan] * tuvw[..., 1])
        x, _ = frac_coord(nx, 1, uvscale[0, chan] * tuvw[..., 0])
        for pol in range(npol):
            # Note that this ordering is different from that in other gridding
            # functions because we want to do a reshape to get the result into
            # the correct order
            # Note also that this is a one-to-many mapping
            tvis[..., chan, pol] = visgrid[y, x, chan, pol]
            twts[..., chan, pol] = wtsgrid[y, x, chan, pol]

    return tvis, twts
